import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from fastapi import FastAPI

from pereklichka.bot.router import create_dispatcher
from pereklichka.bot.worker import DeliveryWorker
from pereklichka.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    token = settings.bot_token.get_secret_value() if settings.bot_token else None
    if settings.bot_token_file is not None:
        token = (await asyncio.to_thread(settings.bot_token_file.read_text)).strip()
    try:
        if token is None:
            yield
            return
        async with Bot(token, session=AiohttpSession(timeout=10)) as bot:
            profile = await bot.get_me()
            dispatcher = create_dispatcher(app.state.sessionmaker, profile.username or "")
            async with asyncio.TaskGroup() as group:
                polling = group.create_task(
                    dispatcher.start_polling(
                        bot,
                        handle_signals=False,
                        close_bot_session=False,
                        allowed_updates=dispatcher.resolve_used_update_types(),
                    )
                )
                delivery = group.create_task(DeliveryWorker(app.state.sessionmaker, bot).run())
                try:
                    yield
                finally:
                    polling.cancel()
                    delivery.cancel()
    finally:
        await app.state.sessionmaker.kw["bind"].dispose()
