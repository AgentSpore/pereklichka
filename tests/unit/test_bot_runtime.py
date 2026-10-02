import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import User
from fastapi import FastAPI

from pereklichka.bot import runtime
from pereklichka.config import Settings


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = FastAPI()
    app.state.settings = Settings(database_url="unused", skill_id="test", _env_file=None)
    app.state.sessionmaker = MagicMock()
    app.state.sessionmaker.kw = {"bind": MagicMock(dispose=AsyncMock())}
    yield app


async def test_runtime_without_token(app: FastAPI) -> None:
    async with runtime.lifespan(app):
        assert app.state.settings.bot_token is None
    app.state.sessionmaker.kw["bind"].dispose.assert_awaited_once()


async def test_lifespan_owns_tasks_and_closes_bot(
    app: FastAPI, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bot-token"
    path.write_text("123456:" + "a" * 35)
    app.state.settings = Settings(
        database_url="unused", skill_id="test", bot_token_file=path, _env_file=None
    )
    finished = []

    async def polling(self, *args, **kwargs) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            finished.append("polling")

    async def deliveries(self) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            finished.append("deliveries")

    bot = Bot("123456:" + "a" * 35)
    bot.get_me = AsyncMock(return_value=User(id=1, is_bot=True, first_name="Test", username="test"))
    bot.session.close = AsyncMock()

    @asynccontextmanager
    async def configured_bot(*args, **kwargs):
        try:
            yield bot
        finally:
            await bot.session.close()

    monkeypatch.setattr(runtime, "Bot", configured_bot)
    monkeypatch.setattr(Dispatcher, "start_polling", polling)
    monkeypatch.setattr(runtime.DeliveryWorker, "run", deliveries)
    async with runtime.lifespan(app):
        await asyncio.sleep(0)
    assert set(finished) == {"polling", "deliveries"}
    bot.session.close.assert_awaited_once()
    app.state.sessionmaker.kw["bind"].dispose.assert_awaited_once()
