import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.app import create_app
from pereklichka.bot.reports import ReportService
from pereklichka.bot.worker import DeliveryWorker
from pereklichka.config import Settings
from pereklichka.db.models import CheckInRow, OutboxRow
from pereklichka.db.outbox import OutboxRepository
from pereklichka.db.relatives import RelativeRepository
from pereklichka.db.repositories import CheckInRepository, WardRepository
from pereklichka.domain.checkin import CheckIn
from pereklichka.domain.family import Ward
from tests.alice_protocol import APPLICATION_ID, SKILL_ID, utterance

NOW = datetime.now(UTC)


@pytest.fixture
async def ward(session: AsyncSession) -> Ward:
    family_id = await RelativeRepository(session).create_family(101)
    await RelativeRepository(session).add_member(family_id, 102)
    ward = Ward(
        family_id=family_id,
        name="Анна",
        checkin_hour=9,
        timezone="Europe/Moscow",
        device_id=APPLICATION_ID,
    )
    await WardRepository(session).add(ward)
    await session.commit()
    return ward


@pytest.fixture
async def checkin(session: AsyncSession, ward: Ward) -> CheckIn:
    checkin = CheckIn(ward_id=ward.id, at=NOW, wellbeing="хорошо", meds_taken=True, needs=None)
    await CheckInRepository(session).add(checkin)
    await session.commit()
    return checkin


async def test_real_alice_dialogue_queues_only_completed_reports(
    database_url: str, session: AsyncSession, ward: Ward
) -> None:
    app = create_app(Settings(database_url=database_url, skill_id=SKILL_ID))
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            state = {}
            for text in ["", "хорошо", "да"]:
                response = await client.post(
                    "/alice", json=utterance(text, new=not state, state=state)
                )
                assert response.status_code == 200
                state = response.json()["session_state"]
            assert await session.scalar(select(func.count()).select_from(OutboxRow)) == 0
            assert await session.scalar(select(CheckInRow.completed_at)) is None
            for _ in range(2):
                response = await client.post("/alice", json=utterance("хлеба", state=state))
                assert response.json()["response"]["end_session"]
            rows = list(await session.scalars(select(OutboxRow)))
            assert len(rows) == 2 and {r.chat_id for r in rows} == {101, 102}
            assert all(
                "Самочувствие: хорошо" in r.text
                and "Лекарства: приняты" in r.text
                and "хлеба" in r.text
                for r in rows
            )
            assert await session.scalar(select(CheckInRow.completed_at)) is not None
    finally:
        await app.state.sessionmaker.kw["bind"].dispose()


async def test_report_rollback_and_replay(
    session: AsyncSession, ward: Ward, checkin: CheckIn
) -> None:
    await ReportService(session, NOW).complete(checkin.id, ward, None)
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(OutboxRow)) == 0
    assert await session.scalar(select(CheckInRow.completed_at)) is None
    await ReportService(session, NOW).complete(checkin.id, ward, None)
    await session.commit()
    await ReportService(session, NOW).complete(checkin.id, ward, "duplicate")
    assert await session.scalar(select(func.count()).select_from(OutboxRow)) == 2
    assert await session.scalar(select(CheckInRow.needs)) is None


async def test_queue_claim_crash_retry_and_fencing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with sessionmaker() as session, session.begin():
        outbox = OutboxRepository(session)
        await outbox.enqueue("event", "report", [101])
        await outbox.enqueue("event", "report", [101])
    async with sessionmaker() as session, session.begin():
        first = await OutboxRepository(session).claim(now + timedelta(seconds=1))
    assert first is not None
    async with sessionmaker() as session, session.begin():
        assert await OutboxRepository(session).claim(now + timedelta(seconds=2)) is None
    async with sessionmaker() as session, session.begin():
        second = await OutboxRepository(session).claim(now + timedelta(seconds=62))
        assert second is not None and second.attempts == 2
        assert not await OutboxRepository(session).ack(first, now)
        assert await OutboxRepository(session).retry(second, now, 10)
    async with sessionmaker() as session, session.begin():
        assert await OutboxRepository(session).claim(now + timedelta(seconds=9)) is None
        third = await OutboxRepository(session).claim(now + timedelta(seconds=10))
        assert third is not None
        assert await OutboxRepository(session).ack(third, now)
        assert await OutboxRepository(session).claim(now + timedelta(days=1)) is None


async def test_concurrent_workers_and_retries(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session, session.begin():
        await OutboxRepository(session).enqueue("event", "report", [101, 102])
    bot = Bot("123456:" + "a" * 35)
    bot.send_message = AsyncMock(
        side_effect=[
            TelegramNetworkError(method=SendMessage(chat_id=101, text="x"), message="offline"),
            True,
        ]
    )
    try:
        async with asyncio.TaskGroup() as group:
            jobs = [
                group.create_task(DeliveryWorker(sessionmaker, bot).deliver_once())
                for _ in range(3)
            ]
        assert sum(job.result() for job in jobs) == 2
        assert bot.send_message.call_count == 2
        async with sessionmaker() as session, session.begin():
            rows = list(await session.scalars(select(OutboxRow)))
            assert sum(r.delivered_at is not None for r in rows) == 1
            pending = next(r for r in rows if r.delivered_at is None)
            assert pending.claim_id is None and pending.attempts == 1
            pending.available_at = await OutboxRepository(session).now() - timedelta(seconds=1)
        bot.send_message.side_effect = None
        bot.send_message.return_value = True
        assert await DeliveryWorker(sessionmaker, bot).deliver_once()
        assert not await DeliveryWorker(sessionmaker, bot).deliver_once()
        async with sessionmaker() as session:
            assert (
                await session.scalar(
                    select(func.count()).where(OutboxRow.delivered_at.is_not(None))
                )
                == 2
            )
    finally:
        await bot.session.close()


async def test_rate_limit_respects_retry_after(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session, session.begin():
        await OutboxRepository(session).enqueue("event", "report", [101])
    bot = Bot("123456:" + "a" * 35)
    bot.send_message = AsyncMock(
        side_effect=TelegramRetryAfter(
            method=SendMessage(chat_id=101, text="x"), message="limit", retry_after=42
        )
    )
    try:
        assert await DeliveryWorker(sessionmaker, bot).deliver_once()
        async with sessionmaker() as session:
            row = await session.scalar(select(OutboxRow))
            assert row is not None and row.delivered_at is None
            assert (row.available_at - await OutboxRepository(session).now()).total_seconds() > 40
    finally:
        await bot.session.close()


async def test_cancelled_worker_leaves_recoverable_lease(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session, session.begin():
        await OutboxRepository(session).enqueue("crash", "report", [101])
    bot = Bot("123456:" + "a" * 35)
    bot.send_message = AsyncMock(side_effect=asyncio.CancelledError)
    try:
        with pytest.raises(asyncio.CancelledError):
            await DeliveryWorker(sessionmaker, bot).deliver_once()
        async with sessionmaker() as session, session.begin():
            recovered = await OutboxRepository(session).claim(
                datetime.now(UTC) + timedelta(seconds=61)
            )
            assert recovered is not None and recovered.attempts == 2
    finally:
        await bot.session.close()


async def test_completion_cancels_leased_silence_only(
    session: AsyncSession, ward: Ward, checkin: CheckIn
) -> None:
    prefix = f"silence:{ward.id}:{NOW.date()}:"
    outbox = OutboxRepository(session)
    await outbox.enqueue(prefix + "first", "alarm", [101])
    await session.commit()
    delivery = await outbox.claim(datetime.now(UTC) + timedelta(seconds=1))
    assert delivery is not None
    await outbox.enqueue("unrelated", "keep", [102])
    await session.commit()
    await ReportService(session, NOW).complete(checkin.id, ward, None)
    await session.commit()
    assert not await outbox.still_claimed(delivery)
    keys = list(await session.scalars(select(OutboxRow.event_key)))
    assert "unrelated" in keys and prefix + "first" not in keys


async def test_worker_run_stops_on_cancel(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    bot = Bot("123456:" + "a" * 35)
    task = asyncio.create_task(DeliveryWorker(sessionmaker, bot).run())
    try:
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await bot.session.close()


async def test_worker_discards_old_day_silence(
    sessionmaker: async_sessionmaker[AsyncSession], ward: Ward
) -> None:
    async with sessionmaker() as session, session.begin():
        day = (await OutboxRepository(session).now() - timedelta(days=1)).date()
        await OutboxRepository(session).enqueue(
            f"silence:{ward.id}:{day}:first", "old alarm", [101]
        )
    bot = Bot("123456:" + "a" * 35)
    bot.send_message = AsyncMock()
    try:
        assert await DeliveryWorker(sessionmaker, bot).deliver_once()
        bot.send_message.assert_not_awaited()
        async with sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(OutboxRow)) == 0
    finally:
        await bot.session.close()


async def test_worker_discards_silence_after_completed_checkin(
    sessionmaker: async_sessionmaker[AsyncSession], ward: Ward, checkin: CheckIn
) -> None:
    async with sessionmaker() as session, session.begin():
        now = await OutboxRepository(session).now()
        await CheckInRepository(session).complete(checkin.id, ward.id, None, now)
        await OutboxRepository(session).enqueue(
            f"silence:{ward.id}:{now.date()}:first", "stale alarm", [101]
        )
    bot = Bot("123456:" + "a" * 35)
    bot.send_message = AsyncMock()
    try:
        assert await DeliveryWorker(sessionmaker, bot).deliver_once()
        bot.send_message.assert_not_awaited()
        async with sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(OutboxRow)) == 0
    finally:
        await bot.session.close()


async def test_worker_sends_current_day_silence(
    sessionmaker: async_sessionmaker[AsyncSession], ward: Ward
) -> None:
    async with sessionmaker() as session, session.begin():
        now = await OutboxRepository(session).now()
        await OutboxRepository(session).enqueue(
            f"silence:{ward.id}:{now.date()}:first", "current alarm", [101]
        )
    bot = Bot("123456:" + "a" * 35)
    bot.send_message = AsyncMock()
    try:
        assert await DeliveryWorker(sessionmaker, bot).deliver_once()
        bot.send_message.assert_awaited_once_with(101, "current alarm", request_timeout=10)
    finally:
        await bot.session.close()
