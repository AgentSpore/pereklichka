from datetime import UTC, datetime

from aiogram import Dispatcher, F, Router
from aiogram.types import Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.bot import texts
from pereklichka.bot.service import FamilyService


def create_dispatcher(sessionmaker: async_sessionmaker[AsyncSession], username: str) -> Dispatcher:
    router = Router()

    @router.message(F.text)
    async def command(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer(texts.PRIVATE_ONLY)
            return
        words = (message.text or "").split(maxsplit=1) or [""]
        command = words[0].split("@")[0].removeprefix("/")
        args = words[1] if len(words) == 2 else ""
        async with sessionmaker() as session, session.begin():
            service = FamilyService(session, message.chat.id, datetime.now(UTC))
            try:
                if command == "start":
                    if args and not args.startswith("join_"):
                        answer = texts.BAD_INVITE
                    else:
                        answer = await service.start(args[5:] if args else None)
                else:
                    answer = await service.command(command, args, username)
            except (ValueError, ValidationError):
                answer = texts.INVALID
        # Commit before Telegram: a network failure must not undo family membership.
        await message.answer(answer)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
