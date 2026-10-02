import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.bot import texts
from pereklichka.db.models import WardRow
from tests.integration.test_bot import bot as bot
from tests.integration.test_bot import dispatcher as dispatcher
from tests.integration.test_bot import send

NOW = datetime(2026, 10, 2, tzinfo=UTC)


@pytest.fixture
async def interact(dispatcher: Dispatcher, bot: Bot):
    async def interaction(value: str, chat_id: int = 101) -> SendMessage:
        if value.startswith("ux:"):
            update = Update(
                update_id=2,
                callback_query=CallbackQuery(
                    id="test",
                    from_user=User(id=abs(chat_id), is_bot=False, first_name="Test"),
                    chat_instance="test",
                    data=value,
                    message=Message(
                        message_id=1,
                        date=NOW,
                        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "group"),
                    ),
                ),
            )
            await dispatcher.feed_update(bot, update)
        else:
            await send(dispatcher, bot, value, chat_id)
        requests = cast(AsyncMock, bot.session.make_request).call_args_list
        return next(
            call.args[1] for call in reversed(requests) if isinstance(call.args[1], SendMessage)
        )

    return interaction


def callback(screen: SendMessage, label: str) -> str:
    assert isinstance(screen.reply_markup, InlineKeyboardMarkup)
    result = next(
        button.callback_data
        for row in screen.reply_markup.inline_keyboard
        for button in row
        if button.text == label
    )
    assert result is not None
    return result


async def test_guided_creation_and_duplicate_confirmation(interact, session: AsyncSession):
    screen = await interact("/start")
    assert callback(screen, texts.ADD) == "ux:add"
    await interact("ux:add")
    screen = await interact("<Анна>")
    assert "2 из" in screen.text
    screen = await interact("25")
    assert "0–23" in screen.text
    screen = await interact("9")
    screen = await interact(callback(screen, texts.MOSCOW))
    assert "&lt;Анна&gt;" in screen.text
    assert await session.scalar(select(func.count()).select_from(WardRow)) == 0
    confirm = callback(screen, texts.CONFIRM_ADD)
    await asyncio.gather(interact(confirm), interact(confirm))
    assert await session.scalar(select(func.count()).select_from(WardRow)) == 1


async def test_cancel_back_invalid_zone_and_stale_draft(interact, session: AsyncSession):
    await interact("/start")
    await interact("ux:add")
    screen = await interact("Анна")
    back = callback(screen, texts.BACK)
    screen = await interact(back)
    assert screen.text == texts.NAME_PROMPT
    await interact("Борис")
    screen = await interact("0")
    old_zone = callback(screen, texts.MOSCOW)
    screen = await interact("No/Zone")
    assert texts.DRAFT_INVALID in screen.text
    await interact("ux:menu")
    await interact("ux:add")
    screen = await interact(old_zone)
    assert screen.text == texts.STALE
    assert await session.scalar(select(func.count()).select_from(WardRow)) == 0
    await interact("/help")
    screen = await interact("9")
    assert screen.text == texts.MENU


async def test_cards_settings_and_family_boundary(interact, session: AsyncSession):
    await interact("/start")
    await interact("/ward Анна | 9 | Europe/Moscow")
    ward = await session.scalar(select(WardRow))
    assert ward is not None
    screen = await interact("ux:wards")
    screen = await interact(callback(screen, "Анна"))
    assert "09:00" in screen.text and texts.UNLINKED in screen.text
    assert str(ward.id) not in screen.text
    screen = await interact(callback(screen, texts.SETTINGS_BUTTON))
    assert "1 из 4" in screen.text
    await interact("10")
    await interact("Asia/Yekaterinburg")
    screen = await interact("0")
    assert "1–1440" in screen.text
    screen = await interact("45")
    screen = await interact(callback(screen, texts.CONFIRM_SAVE))
    assert "10:00" in screen.text and "45 мин." in screen.text
    await session.refresh(ward)
    assert (ward.checkin_hour, ward.timezone, ward.escalation_minutes) == (
        10,
        "Asia/Yekaterinburg",
        45,
    )
    await interact("/start", 102)
    for action in ["ward", "code", "settings"]:
        screen = await interact(f"ux:{action}:{ward.id}", 102)
        assert screen.text == texts.UX_NO_ACCESS
    screen = await interact("ux:code:broken")
    assert screen.text == texts.UX_NO_ACCESS
    screen = await interact("ux:unknown")
    assert screen.text == texts.STALE
    screen = await interact("ux:add", -101)
    assert screen.text == texts.PRIVATE_ONLY
    assert await session.scalar(select(func.count()).select_from(WardRow)) == 1


async def test_new_relatives_hide_chat_ids_and_invitation_works(interact):
    await interact("/start")
    screen = await interact("ux:invite")
    token = screen.text.split("join_")[1]
    await interact("/start join_" + token, 102)
    screen = await interact("ux:relatives")
    assert "Вы" in screen.text and "Родственник 2" in screen.text
    assert "101" not in screen.text and "102" not in screen.text


async def test_export_actual_synthetic_screens(interact, session: AsyncSession):
    screens = []
    screen = await interact("/start")
    screens.append(("menu", screen))
    screen = await interact("ux:add")
    screens.append(("name", screen))
    screen = await interact("Анна & <Ивановна> " + "с длинным именем " * 4)
    screens.append(("hour", screen))
    screen = await interact("9")
    screens.append(("timezone", screen))
    screen = await interact(callback(screen, texts.MOSCOW))
    screens.append(("confirmation", screen))
    screen = await interact(callback(screen, texts.CONFIRM_ADD))
    screens.append(("card", screen))
    screen = await interact(callback(screen, texts.CODE_BUTTON))
    assert "Код привязки" in screen.text
    screens.append(("link_code", screen))
    screen = await interact(callback(screen, texts.SETTINGS_BUTTON))
    screens.append(("settings", screen))
    await interact("ux:menu")
    screen = await interact("ux:wards")
    screens.append(("ward_list", screen))
    assert await session.scalar(select(func.count()).select_from(WardRow)) == 1
    Path("/tmp/pereklichka-ux-preview.json").write_text(
        json.dumps(
            [
                {
                    "label": label,
                    "payload": screen.model_dump(
                        mode="json", exclude_defaults=True, exclude_none=True
                    ),
                }
                for label, screen in screens
            ],
            ensure_ascii=False,
        )
    )
