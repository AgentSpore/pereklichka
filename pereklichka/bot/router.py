from datetime import UTC, datetime
from html import escape
from uuid import UUID

from aiogram import Dispatcher, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.bot import flows, presentation, texts
from pereklichka.bot.schemas import Screen
from pereklichka.bot.service import FamilyService


def create_dispatcher(sessionmaker: async_sessionmaker[AsyncSession], username: str) -> Dispatcher:
    """Serialize each user's draft and keep database commits ahead of Telegram sends."""
    router = Router()
    router.message.register(command)
    router.callback_query.register(callback)
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dispatcher["sessionmaker"] = sessionmaker
    dispatcher["username"] = username
    dispatcher.include_router(router)
    return dispatcher


async def command(
    message: Message,
    state: FSMContext,
    sessionmaker: async_sessionmaker[AsyncSession],
    username: str,
) -> None:
    if message.chat.type != "private":
        await message.answer(texts.PRIVATE_ONLY)
        return
    value = message.text or ""
    if not value:
        return
    words = value.split(maxsplit=1) or [""]
    name = words[0].split("@")[0].removeprefix("/")
    args = words[1] if len(words) == 2 else ""
    if value.startswith("/") and name == "privacy":
        await message.answer(texts.PRIVACY, reply_markup=presentation.menu())
        return
    async with sessionmaker() as session, session.begin():
        service = FamilyService(session, message.chat.id, datetime.now(UTC))
        if not value.startswith("/") and await state.get_state():
            screen = await flows.enter_value(state, value)
        elif not value.startswith("/"):
            await state.clear()
            screen = Screen(texts.MENU, presentation.menu())
        else:
            await state.clear()
            try:
                if name == "start":
                    answer = (
                        texts.BAD_INVITE
                        if args and not args.startswith("join_")
                        else (await service.start(args[5:] if args else None))
                    )
                else:
                    answer = await service.command(name, args, username)
            except (ValueError, ValidationError):
                answer = texts.INVALID
            screen = Screen(escape(answer), presentation.menu())
    if screen.clear_draft:
        await state.clear()
    await message.answer(screen.text, reply_markup=screen.markup, parse_mode="HTML")


async def callback(
    query: CallbackQuery,
    state: FSMContext,
    sessionmaker: async_sessionmaker[AsyncSession],
    username: str,
) -> None:
    await query.answer()
    message = query.message
    if not isinstance(message, Message):
        return
    if message.chat.type != "private" or query.from_user.id != message.chat.id:
        await message.answer(texts.PRIVATE_ONLY)
        return
    parts = (query.data or "").split(":")
    async with sessionmaker() as session, session.begin():
        service = FamilyService(session, message.chat.id, datetime.now(UTC))
        try:
            if len(parts) < 2 or parts[0] != "ux":
                screen = Screen(texts.STALE, presentation.navigation())
            elif parts[1] == "draft":
                screen = await flows.draft_action(state, service, parts)
            else:
                await state.clear()
                if parts[1] == "add" and len(parts) == 2:
                    screen = await flows.begin_draft(state, service, None)
                elif parts[1] in {"ward", "code", "settings"} and len(parts) == 3:
                    ward_id = UUID(parts[2])
                    screen = (
                        await flows.begin_draft(state, service, ward_id)
                        if parts[1] == "settings"
                        else (await flows.ward_screen(parts[1], ward_id, service))
                    )
                elif len(parts) == 2:
                    screen = await flows.open_screen(parts[1], service, username)
                else:
                    screen = Screen(texts.STALE, presentation.navigation())
        except (ValueError, ValidationError):
            screen = Screen(texts.UX_NO_ACCESS, presentation.navigation())
    if screen.clear_draft:
        await state.clear()
    await message.answer(screen.text, reply_markup=screen.markup, parse_mode="HTML")
