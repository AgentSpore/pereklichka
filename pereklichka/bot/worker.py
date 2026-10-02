import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.db.outbox import OutboxRepository


class DeliveryWorker:
    """Claims are committed before sending; abandoned claims expire after a crash."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], bot: Bot) -> None:
        self._sessions = sessionmaker
        self._bot = bot

    async def run(self) -> None:
        while True:
            try:
                delivered = await self.deliver_once()
            except SQLAlchemyError:
                logger.warning("Delivery database unavailable; retrying")
                delivered = False
            if not delivered:
                await asyncio.sleep(1)

    async def deliver_once(self) -> bool:
        async with self._sessions() as session, session.begin():
            outbox = OutboxRepository(session)
            delivery = await outbox.claim(await outbox.now())
        if delivery is None:
            return False
        async with self._sessions() as session, session.begin():
            outbox = OutboxRepository(session)
            if await outbox.discard_if_stale(delivery) or not await outbox.still_claimed(delivery):
                return True
        delay = 0
        try:
            await self._bot.send_message(delivery.chat_id, delivery.text, request_timeout=10)
        except TelegramRetryAfter as error:
            delay = max(error.retry_after, 1)
        except (TelegramAPIError, TimeoutError, OSError):
            delay = min(2 ** min(delivery.attempts, 12), 3600)
        async with self._sessions() as session, session.begin():
            outbox = OutboxRepository(session)
            if delay:
                updated = await outbox.retry(delivery, await outbox.now(), delay)
            else:
                updated = await outbox.ack(delivery, await outbox.now())
        if not updated:
            logger.warning("Delivery claim expired before acknowledgement")
        return True
