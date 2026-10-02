import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.bot import texts
from pereklichka.bot.router import create_dispatcher
from pereklichka.bot.service import FamilyService
from pereklichka.db.models import FamilyRow, InvitationRow, LinkCodeRow, RelativeRow, WardRow
from pereklichka.db.relatives import RelativeRepository

NOW = datetime(2026, 10, 2, 8, tzinfo=UTC)


@pytest.fixture
async def bot() -> AsyncIterator[Bot]:
    bot = Bot("123456:" + "a" * 35)
    bot.session.make_request = AsyncMock(
        return_value=Message(message_id=1, date=NOW, chat=Chat(id=101, type="private"))
    )
    yield bot
    await bot.session.close()


@pytest.fixture
async def dispatcher(sessionmaker: async_sessionmaker[AsyncSession]) -> Dispatcher:
    return create_dispatcher(sessionmaker, "pereklichka_test_bot")


async def send(dispatcher: Dispatcher, bot: Bot, command: str, chat_id: int = 101) -> str:
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=NOW,
            chat=Chat(id=chat_id, type="private" if chat_id > 0 else "group"),
            from_user=User(id=abs(chat_id), is_bot=False, first_name="Test"),
            text=command,
        ),
    )
    await dispatcher.feed_update(bot, update)
    return cast(AsyncMock, bot.session.make_request).call_args.args[1].text


async def test_commands_run_dispatcher_real_db(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession
) -> None:
    assert await send(dispatcher, bot, "/wards") == texts.NO_FAMILY
    assert await send(dispatcher, bot, "/start") == texts.FAMILY_READY
    await send(dispatcher, bot, "/start")
    assert await session.scalar(select(func.count()).select_from(FamilyRow)) == 1
    assert "добавлен" in await send(dispatcher, bot, "/ward Анна | 9 | Europe/Moscow")
    ward = await session.scalar(select(WardRow))
    assert ward is not None
    assert str(ward.id) in await send(dispatcher, bot, "/wards")
    assert "101" in await send(dispatcher, bot, "/relatives")
    assert (
        await send(dispatcher, bot, "/schedule " + str(ward.id) + " 10 Asia/Vladivostok 45")
        == texts.SAVED
    )
    await session.refresh(ward)
    assert (ward.checkin_hour, ward.timezone, ward.escalation_minutes) == (
        10,
        "Asia/Vladivostok",
        45,
    )
    assert await send(dispatcher, bot, "/order 101") == texts.SAVED
    assert "Код привязки" in await send(dispatcher, bot, "/code " + str(ward.id))
    code = await session.scalar(select(LinkCodeRow))
    assert code is not None and len(code.code) == 6 and code.code.isdigit()
    assert timedelta(minutes=14) < code.expires_at - datetime.now(UTC) <= timedelta(minutes=15)
    assert await send(dispatcher, bot, "/unknown") == texts.HELP


@pytest.mark.parametrize(
    "command",
    [
        "/ward | 9 | Europe/Moscow",
        "/ward Анна | 24 | Europe/Moscow",
        "/ward Анна | -1 | Europe/Moscow",
        "/ward Анна | 9 | Not/AZone",
        "/ward " + "а" * 101 + " | 9 | Europe/Moscow",
        "/ward broken",
        "/schedule bad",
        "/code broken",
        "/order 101 101",
    ],
)
async def test_bad_input_is_handled(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, command: str
) -> None:
    await send(dispatcher, bot, "/start")
    assert await send(dispatcher, bot, command) == texts.INVALID
    assert await session.scalar(select(func.count()).select_from(WardRow)) == 0


async def test_private_only_and_cross_family_access(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession
) -> None:
    assert await send(dispatcher, bot, "/start", -1) == texts.PRIVATE_ONLY
    assert await session.scalar(select(func.count()).select_from(FamilyRow)) == 0
    await send(dispatcher, bot, "/start")
    await send(dispatcher, bot, "/ward Анна | 9 | Europe/Moscow")
    ward_id = await session.scalar(select(WardRow.id))
    await send(dispatcher, bot, "/start", 102)
    assert await send(dispatcher, bot, f"/code {ward_id}", 102) == texts.NO_ACCESS
    assert (
        await send(dispatcher, bot, f"/schedule {ward_id} 12 Europe/Moscow 5", 102)
        == texts.NO_ACCESS
    )
    assert await send(dispatcher, bot, "/wards", 102) == texts.EMPTY
    assert await send(dispatcher, bot, "/order 101", 102) == texts.INVALID


async def test_invitation_dispatcher_replay_and_other_family(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession
) -> None:
    await send(dispatcher, bot, "/start")
    invitation = await send(dispatcher, bot, "/invite")
    token = invitation.split("join_")[1]
    assert len(token) == 32
    stored = await session.scalar(select(InvitationRow))
    assert stored is not None and stored.token_hash != token
    assert await send(dispatcher, bot, "/start join_" + token, 102) == texts.JOINED
    assert await send(dispatcher, bot, "/start join_" + token, 102) == texts.JOINED
    assert await session.scalar(select(func.count()).select_from(RelativeRow)) == 2
    assert await send(dispatcher, bot, "/order 102 101") == texts.SAVED
    assert "1. 102" in await send(dispatcher, bot, "/relatives")
    await send(dispatcher, bot, "/start", 103)
    assert await send(dispatcher, bot, "/start join_" + token, 103) == texts.OTHER_FAMILY
    assert await send(dispatcher, bot, "/start join_invalid", 104) == texts.BAD_INVITE
    assert await send(dispatcher, bot, "/start invalid", 104) == texts.BAD_INVITE


async def test_invitation_expiry_and_concurrent_join(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session, session.begin():
        owner = FamilyService(session, 101, NOW)
        await owner.start(None)
        invitation = await owner.command("invite", "", "test")
    token = invitation.split("join_")[1]

    async def join(chat_id: int, now: datetime = NOW) -> str:
        async with sessionmaker() as session, session.begin():
            return await FamilyService(session, chat_id, now).start(token)

    async with asyncio.TaskGroup() as group:
        jobs = [group.create_task(join(chat_id)) for chat_id in [102, 102, 103, 104]]
    assert [job.result() for job in jobs] == [texts.JOINED] * 4
    assert await join(105, NOW + timedelta(days=1)) == texts.BAD_INVITE
    async with sessionmaker() as session:
        members = list(await session.scalars(select(RelativeRow).order_by(RelativeRow.alert_order)))
        assert len(members) == 4
        assert [m.alert_order for m in members] == [1, 2, 3, 4]


async def test_concurrent_family_creation(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async def start() -> None:
        async with sessionmaker() as session, session.begin():
            await FamilyService(session, 101, NOW).start(None)

    async with asyncio.TaskGroup() as group:
        for _ in range(5):
            group.create_task(start())
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(FamilyRow)) == 1
        assert await RelativeRepository(session).family_for(101) is not None
        assert await session.scalar(select(func.count()).select_from(RelativeRow)) == 1


async def test_unknown_ward(dispatcher: Dispatcher, bot: Bot) -> None:
    await send(dispatcher, bot, "/start")
    assert await send(dispatcher, bot, f"/code {uuid4()}") == texts.NO_ACCESS


async def test_service_commands_direct(session: AsyncSession) -> None:
    service = FamilyService(session, 101, NOW)
    await service.start(None)
    await service.start(None)
    await service.command("ward", "Анна | 9 | Europe/Moscow", "test")
    ward_id = await session.scalar(select(WardRow.id))
    assert "Анна" in await service.command("wards", "", "test")
    assert "101" in await service.command("relatives", "", "test")
    assert await service.command("order", "101", "test") == texts.SAVED
    assert await service.command("order", "102", "test") == texts.INVALID
    assert "Код" in await service.command("code", str(ward_id), "test")
    assert await service.command("code", str(uuid4()), "test") == texts.NO_ACCESS
    assert (
        await service.command("schedule", f"{uuid4()} 10 Europe/Moscow 1", "test")
        == texts.NO_ACCESS
    )
    assert await service.command("schedule", f"{ward_id} 10 Europe/Moscow 1", "test") == texts.SAVED
    token = (await service.command("invite", "", "test")).split("join_")[1]
    other = FamilyService(session, 102, NOW)
    assert await other.command("wards", "", "test") == texts.NO_FAMILY
    assert await other.start(token) == texts.JOINED
    assert await other.start(token) == texts.JOINED
    outsider = FamilyService(session, 103, NOW)
    await outsider.start(None)
    assert await outsider.start(token) == texts.OTHER_FAMILY
    assert await outsider.start("invalid") == texts.BAD_INVITE
    assert await service.command("unknown", "", "test") == texts.HELP
