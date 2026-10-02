import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from fastapi import FastAPI

from pereklichka.bot.router import create_dispatcher
from pereklichka.bot.worker import DeliveryWorker
from pereklichka.config import Settings
from pereklichka.scheduler.runner import SilenceScheduler


async def drain_handlers(dispatcher: Dispatcher) -> None:
    """Finish handlers after polling stops, before closing Telegram and database sessions."""
    for task in tuple(dispatcher._handle_update_tasks):
        with suppress(asyncio.CancelledError):
            await task


async def stop_polling(dispatcher: Dispatcher, polling: asyncio.Task) -> None:
    """Allow startup to enter the running state before asking aiogram to stop."""
    await asyncio.sleep(0)
    if not polling.done():
        await dispatcher.stop_polling()
    await polling


async def run_polling(dispatcher: Dispatcher, bot: Bot) -> None:
    """Shield aiogram's wrapper so its own stop signal cleans up internal polling tasks."""
    dispatcher.shutdown.register(drain_handlers)
    polling = asyncio.create_task(
        dispatcher.start_polling(
            bot,
            handle_signals=False,
            close_bot_session=False,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    )
    try:
        await asyncio.shield(polling)
    finally:
        cleanup = asyncio.create_task(stop_polling(dispatcher, polling))
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        await cleanup
        if interrupted:
            raise asyncio.CancelledError


@asynccontextmanager
async def run_bot(app: FastAPI, token: str) -> AsyncIterator[None]:
    """Own polling and delivery workers until the application shuts down."""
    async with Bot(token, session=AiohttpSession(timeout=10)) as bot:
        profile = await bot.get_me()
        dispatcher = create_dispatcher(app.state.sessionmaker, profile.username or "")
        async with asyncio.TaskGroup() as group:
            polling = group.create_task(run_polling(dispatcher, bot))
            delivery = group.create_task(DeliveryWorker(app.state.sessionmaker, bot).run())
            try:
                yield
            finally:
                polling.cancel()
                delivery.cancel()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Stop active jobs and handlers before disposing the shared database engine."""
    settings: Settings = app.state.settings
    scheduler = SilenceScheduler(app.state.sessionmaker)
    try:
        scheduler.start()
        token = settings.bot_token.get_secret_value() if settings.bot_token else None
        if settings.bot_token_file is not None:
            token = (await asyncio.to_thread(settings.bot_token_file.read_text)).strip()
        if token is None:
            yield
        else:
            async with run_bot(app, token):
                yield
    finally:
        try:
            await scheduler.stop()
        finally:
            await app.state.sessionmaker.kw["bind"].dispose()
