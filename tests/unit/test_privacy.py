from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from pereklichka.app import create_app
from pereklichka.bot import texts
from pereklichka.bot.router import create_dispatcher
from pereklichka.config import Settings


def test_policy_is_public_without_database_or_tracking() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://unused@127.0.0.1/unused",
        skill_id="privacy-test",
        _env_file=None,
    )
    client = TestClient(create_app(settings))
    response = client.get("/privacy")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'lang="ru"' in response.text and 'name="viewport"' in response.text
    assert "Roman Konnov" in response.text and "https://t.me/exzentttt" in response.text
    assert "5 октября 2026" in response.text
    assert "УТОЧНИТЬ" not in response.text and "Черновик: реквизиты" not in response.text
    assert "[[" not in response.text
    assert "автоматическое удаление истории не настроено" in response.text
    assert "<script" not in response.text and "<iframe" not in response.text
    assert "set-cookie" not in response.headers
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("chat_type", ["private", "group"])
async def test_privacy_before_start_preserves_draft_without_database(chat_type: str) -> None:
    bot = Bot("123456:" + "a" * 35)
    bot.session.make_request = AsyncMock(return_value=True)
    dispatcher = create_dispatcher(async_sessionmaker(), "test_bot")
    state = dispatcher.fsm.get_context(bot=bot, chat_id=101, user_id=101)
    await state.set_state("hour")
    await state.set_data({"name": "Synthetic", "nonce": "test"})
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=101, type=chat_type),
        from_user=User(id=101, is_bot=False, first_name="Test"),
        text="/privacy",
    )
    try:
        await dispatcher.feed_update(bot, Update(update_id=1, message=message))
        answer = bot.session.make_request.call_args.args[1].text
        if chat_type == "private":
            assert "https://pereklichka.agentspore.com/privacy" in answer
            assert "https://t.me/exzentttt" in answer
        else:
            assert answer == texts.PRIVATE_ONLY
        assert await state.get_state() == "hour"
        assert await state.get_data() == {"name": "Synthetic", "nonce": "test"}
    finally:
        await dispatcher.storage.close()
        await dispatcher.fsm.events_isolation.close()
        await bot.session.close()


def test_help_discloses_policy_and_contact() -> None:
    for text in (texts.HELP, texts.UX_HELP):
        assert "https://pereklichka.agentspore.com/privacy" in text
        assert "https://t.me/exzentttt" in text
