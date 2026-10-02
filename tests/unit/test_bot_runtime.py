import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User
from fastapi import FastAPI

from pereklichka.bot import runtime
from pereklichka.config import Settings


@pytest.fixture
async def app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    app = FastAPI()
    app.state.settings = Settings(database_url="unused", skill_id="test", _env_file=None)
    app.state.sessionmaker = MagicMock()
    app.state.sessionmaker.kw = {"bind": MagicMock(dispose=AsyncMock())}
    scheduler = MagicMock(stop=AsyncMock())
    monkeypatch.setattr(runtime, "SilenceScheduler", lambda sessions: scheduler)
    monkeypatch.setattr(runtime.DeliveryWorker, "run", lambda self: asyncio.Event().wait())
    app.state.scheduler = scheduler
    yield app


async def test_runtime_without_token(app: FastAPI) -> None:
    async with runtime.lifespan(app):
        assert app.state.settings.bot_token is None
    app.state.scheduler.start.assert_called_once()
    app.state.scheduler.stop.assert_awaited_once()
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
    started = asyncio.Event()
    owned = []

    async def polling(self, *args, **kwargs) -> None:
        owned.append(asyncio.current_task())
        started.set()
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
    monkeypatch.setattr(Dispatcher, "_polling", polling)
    monkeypatch.setattr(runtime.DeliveryWorker, "run", deliveries)
    try:
        async with runtime.lifespan(app):
            await asyncio.wait_for(started.wait(), 1)
        assert set(finished) == {"polling", "deliveries"}
        assert all(task.done() for task in owned)
    finally:
        for task in owned:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    continue
    bot.session.close.assert_awaited_once()
    app.state.sessionmaker.kw["bind"].dispose.assert_awaited_once()


@pytest.fixture
def update() -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=101, type="private"),
            from_user=User(id=101, is_bot=False, first_name="Test"),
            text="test",
        ),
    )


async def test_polling_drains_active_handler_before_bot_and_database_close(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, update: Update
) -> None:
    dispatcher = Dispatcher()
    handler_started, polling_stopped, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    @dispatcher.message()
    async def handler(message: Message) -> None:
        handler_started.set()
        await release.wait()
        events.append("handler")

    async def polling(self, bot, **kwargs) -> None:
        task = asyncio.create_task(self.feed_update(bot, update))
        self._handle_update_tasks.add(task)
        task.add_done_callback(self._handle_update_tasks.discard)
        try:
            await asyncio.Event().wait()
        finally:
            polling_stopped.set()

    bot = Bot("123456:" + "a" * 35)
    bot.get_me = AsyncMock(return_value=User(id=1, is_bot=True, first_name="Test", username="test"))
    bot.session.close = AsyncMock(side_effect=lambda: events.append("bot"))
    app.state.sessionmaker.kw["bind"].dispose = AsyncMock(
        side_effect=lambda: events.append("database")
    )
    app.state.settings = Settings(
        database_url="unused", skill_id="test", bot_token="123456:" + "a" * 35, _env_file=None
    )

    @asynccontextmanager
    async def configured_bot(*args, **kwargs):
        try:
            yield bot
        finally:
            await bot.session.close()

    async def release_handler() -> None:
        await polling_stopped.wait()
        assert events == []
        release.set()

    monkeypatch.setattr(runtime, "Bot", configured_bot)
    monkeypatch.setattr(runtime, "create_dispatcher", lambda *args: dispatcher)
    monkeypatch.setattr(Dispatcher, "_polling", polling)
    releasing = asyncio.create_task(release_handler())
    async with runtime.lifespan(app):
        await asyncio.wait_for(handler_started.wait(), 1)
    await releasing
    assert events == ["handler", "bot", "database"]
    assert not dispatcher._handle_update_tasks


async def test_repeated_polling_cancellation_cleans_internal_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = Dispatcher()
    bot = Bot("123456:" + "a" * 35)
    started, handler_release = asyncio.Event(), asyncio.Event()
    owned = []

    async def polling(self, **kwargs) -> None:
        owned.append(asyncio.current_task())
        handler = asyncio.create_task(handler_release.wait())
        self._handle_update_tasks.add(handler)
        handler.add_done_callback(self._handle_update_tasks.discard)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Dispatcher, "_polling", polling)
    task = asyncio.create_task(runtime.run_polling(dispatcher, bot))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        handler_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert all(task.done() for task in owned)
        assert not dispatcher._handle_update_tasks
    finally:
        handler_release.set()
        await bot.session.close()


async def test_polling_startup_error_does_not_leak_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = Dispatcher()
    bot = Bot("123456:" + "a" * 35)

    async def fail_startup(**kwargs) -> None:
        raise ValueError("startup failure")

    dispatcher.startup.register(fail_startup)
    try:
        before = asyncio.all_tasks()
        with pytest.raises(ValueError, match="startup failure"):
            await runtime.run_polling(dispatcher, bot)
        assert not (asyncio.all_tasks() - before)
    finally:
        await bot.session.close()


async def test_scheduler_stops_before_database_disposal_without_bot(app: FastAPI) -> None:
    order = []
    app.state.scheduler.stop = AsyncMock(side_effect=lambda: order.append("scheduler"))
    app.state.sessionmaker.kw["bind"].dispose = AsyncMock(
        side_effect=lambda: order.append("database")
    )
    async with runtime.lifespan(app):
        assert order == []
    assert order == ["scheduler", "database"]


async def test_missing_token_file_still_closes_scheduler_and_engine(app: FastAPI, tmp_path) -> None:
    app.state.settings = Settings(
        database_url="unused", skill_id="test", bot_token_file=tmp_path / "missing", _env_file=None
    )
    with pytest.raises(FileNotFoundError):
        async with runtime.lifespan(app):
            pytest.fail("missing token file must fail startup")
    app.state.scheduler.stop.assert_awaited_once()
    app.state.sessionmaker.kw["bind"].dispose.assert_awaited_once()


async def test_scheduler_shutdown_error_still_disposes_engine(app: FastAPI) -> None:
    app.state.scheduler.stop.side_effect = RuntimeError("shutdown failure")
    with pytest.raises(RuntimeError, match="shutdown failure"):
        async with runtime.lifespan(app):
            pass
    app.state.sessionmaker.kw["bind"].dispose.assert_awaited_once()
