import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.db.alerts import SilenceAlertRow
from pereklichka.db.models import CheckInRow, FamilyRow, OutboxRow, RelativeRow, WardRow
from pereklichka.db.outbox import OutboxRepository
from pereklichka.scheduler.runner import SilenceScheduler

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)


@pytest.fixture
async def linked_ward(sessionmaker: async_sessionmaker[AsyncSession]) -> WardRow:
    async with sessionmaker() as session, session.begin():
        family = FamilyRow(id=uuid4())
        session.add(family)
        await session.flush()
        ward = WardRow(
            id=uuid4(),
            family_id=family.id,
            name="Анна",
            checkin_hour=9,
            timezone="Europe/Moscow",
            device_id="speaker",
            consent_at=NOW,
            consent_version="v1",
            escalation_minutes=30,
        )
        session.add(ward)
        session.add_all(
            [
                RelativeRow(id=uuid4(), family_id=family.id, chat_id=20, alert_order=2),
                RelativeRow(id=uuid4(), family_id=family.id, chat_id=10, alert_order=1),
            ]
        )
    return ward


async def test_missing_checkin_first_then_all_after_delay(sessionmaker, linked_ward) -> None:
    scheduler = SilenceScheduler(sessionmaker)
    await scheduler.tick(NOW - timedelta(seconds=1))
    async with sessionmaker() as session:
        assert list(await session.scalars(select(OutboxRow))) == []
    await scheduler.tick(NOW)
    await scheduler.tick(NOW + timedelta(minutes=29, seconds=59))
    async with sessionmaker() as session:
        [first] = list(await session.scalars(select(OutboxRow)))
        assert first.chat_id == 10
        assert first.event_key.endswith(":first")
    await scheduler.tick(NOW + timedelta(minutes=30))
    await scheduler.tick(NOW + timedelta(hours=2))
    async with sessionmaker() as session:
        rows = list(await session.scalars(select(OutboxRow)))
        assert sorted((r.chat_id, r.event_key.rsplit(":", 1)[1]) for r in rows) == [
            (10, "all"),
            (10, "first"),
            (20, "all"),
        ]
        [alert] = list(await session.scalars(select(SilenceAlertRow)))
        assert alert.first_at == NOW
        assert alert.escalated_at == NOW + timedelta(minutes=30)


@pytest.mark.parametrize("completed", [False, True])
async def test_only_completed_local_day_checkin_suppresses_alert(
    sessionmaker, linked_ward, completed
):
    async with sessionmaker() as session, session.begin():
        session.add(
            CheckInRow(
                id=uuid4(),
                ward_id=linked_ward.id,
                at=NOW - timedelta(minutes=5),
                wellbeing="хорошо",
                meds_taken=None,
                needs=None,
                completed_at=NOW - timedelta(minutes=1) if completed else None,
            )
        )
    await SilenceScheduler(sessionmaker).tick(NOW)
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(OutboxRow)))) == (0 if completed else 1)


async def test_parallel_sweeps_do_not_duplicate(sessionmaker, linked_ward):
    scheduler = SilenceScheduler(sessionmaker)
    async with asyncio.TaskGroup() as tasks:
        for _ in range(5):
            tasks.create_task(scheduler.tick(NOW))
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(OutboxRow)))) == 1
        assert len(list(await session.scalars(select(SilenceAlertRow)))) == 1


@pytest.mark.parametrize("field", ["device_id", "consent_at"])
async def test_unlinked_or_nonconsenting_ward_has_no_alert(sessionmaker, linked_ward, field):
    async with sessionmaker() as session, session.begin():
        row = await session.get(WardRow, linked_ward.id)
        setattr(row, field, None)
    await SilenceScheduler(sessionmaker).tick(NOW)
    async with sessionmaker() as session:
        assert list(await session.scalars(select(OutboxRow))) == []


async def test_completion_cancels_queued_alert_and_escalation(sessionmaker, linked_ward):
    scheduler = SilenceScheduler(sessionmaker)
    await scheduler.tick(NOW)
    async with sessionmaker() as session, session.begin():
        session.add(
            CheckInRow(
                id=uuid4(),
                ward_id=linked_ward.id,
                at=NOW,
                wellbeing="хорошо",
                meds_taken=True,
                needs=None,
                completed_at=NOW + timedelta(minutes=5),
            )
        )
    await scheduler.tick(NOW + timedelta(minutes=30))
    async with sessionmaker() as session:
        assert list(await session.scalars(select(OutboxRow))) == []
        [alert] = list(await session.scalars(select(SilenceAlertRow)))
        assert alert.escalated_at is None


async def test_local_midnight_ignores_previous_day_completion(sessionmaker, linked_ward):
    midnight = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)
    async with sessionmaker() as session, session.begin():
        row = await session.get(WardRow, linked_ward.id)
        row.checkin_hour = 0
        session.add(
            CheckInRow(
                id=uuid4(),
                ward_id=row.id,
                at=midnight - timedelta(minutes=1),
                wellbeing="хорошо",
                meds_taken=True,
                needs=None,
                completed_at=midnight - timedelta(seconds=1),
            )
        )
    await SilenceScheduler(sessionmaker).tick(midnight)
    async with sessionmaker() as session:
        [alert] = list(await session.scalars(select(SilenceAlertRow)))
        assert alert.local_day.isoformat() == "2026-10-03"


async def test_downtime_catchup_starts_escalation_delay_at_first_notification(
    sessionmaker, linked_ward
):
    resumed = NOW + timedelta(hours=3)
    scheduler = SilenceScheduler(sessionmaker)
    await scheduler.tick(resumed)
    await scheduler.tick(resumed + timedelta(minutes=29))
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(OutboxRow)))) == 1
    await scheduler.tick(resumed + timedelta(minutes=30))
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(OutboxRow)))) == 3


async def test_scheduler_start_stop_does_not_leave_job_running(sessionmaker):
    scheduler = SilenceScheduler(sessionmaker)
    scheduler.start()
    await asyncio.sleep(0)
    await scheduler.stop()
    await scheduler.stop()


async def test_queue_failure_rolls_back_alert_record(sessionmaker, linked_ward, monkeypatch):
    enqueue = AsyncMock(side_effect=RuntimeError("queue unavailable"))
    monkeypatch.setattr(OutboxRepository, "enqueue", enqueue)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await SilenceScheduler(sessionmaker).tick(NOW)
    async with sessionmaker() as session:
        assert list(await session.scalars(select(SilenceAlertRow))) == []
        assert list(await session.scalars(select(OutboxRow))) == []


async def test_settings_change_and_new_day_produce_only_due_alerts(sessionmaker, linked_ward):
    scheduler = SilenceScheduler(sessionmaker)
    await scheduler.tick(NOW)
    async with sessionmaker() as session, session.begin():
        ward = await session.get(WardRow, linked_ward.id)
        ward.checkin_hour = 12
        ward.escalation_minutes = 60
    await scheduler.tick(NOW + timedelta(hours=1))
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(OutboxRow)))) == 1
    await scheduler.tick(NOW + timedelta(hours=3))
    await scheduler.tick(NOW + timedelta(days=1, hours=2))
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(SilenceAlertRow)))) == 1
    await scheduler.tick(NOW + timedelta(days=1, hours=3))
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(SilenceAlertRow)))) == 2


async def test_sweep_skips_ward_while_completion_transaction_holds_lock(sessionmaker, linked_ward):
    scheduler = SilenceScheduler(sessionmaker)
    async with sessionmaker() as completing, completing.begin():
        await completing.scalar(
            select(WardRow.id).where(WardRow.id == linked_ward.id).with_for_update()
        )
        completing.add(
            CheckInRow(
                id=uuid4(),
                ward_id=linked_ward.id,
                at=NOW - timedelta(minutes=1),
                wellbeing="хорошо",
                meds_taken=True,
                needs=None,
                completed_at=NOW,
            )
        )
        await completing.flush()
        await asyncio.wait_for(scheduler.tick(NOW), timeout=2)
    await scheduler.tick(NOW)
    async with sessionmaker() as session:
        assert list(await session.scalars(select(OutboxRow))) == []
        assert list(await session.scalars(select(SilenceAlertRow))) == []


async def test_sweep_pages_beyond_one_batch(sessionmaker, linked_ward):
    async with sessionmaker() as session, session.begin():
        session.add_all(
            [
                WardRow(
                    id=uuid4(),
                    family_id=linked_ward.family_id,
                    name="Близкий",
                    checkin_hour=9,
                    timezone="Europe/Moscow",
                    device_id=f"speaker-{number}",
                    consent_at=NOW,
                    consent_version="v1",
                    escalation_minutes=30,
                )
                for number in range(100)
            ]
        )
    await SilenceScheduler(sessionmaker).tick(NOW)
    async with sessionmaker() as session:
        assert len(list(await session.scalars(select(SilenceAlertRow)))) == 101
        assert len(list(await session.scalars(select(OutboxRow)))) == 101


async def test_escalation_crosses_midnight_without_new_initial_alert(sessionmaker, linked_ward):
    first = datetime(2026, 10, 2, 20, tzinfo=UTC)
    async with sessionmaker() as session, session.begin():
        ward = await session.get(WardRow, linked_ward.id)
        ward.checkin_hour = 23
        ward.escalation_minutes = 120
    scheduler = SilenceScheduler(sessionmaker)
    await scheduler.tick(first)
    await scheduler.tick(first + timedelta(minutes=119))
    await scheduler.tick(first + timedelta(minutes=120))
    async with sessionmaker() as session:
        rows = list(await session.scalars(select(OutboxRow)))
        assert sorted(r.event_key.rsplit(":", 1)[1] for r in rows) == ["all", "all", "first"]
        [alert] = list(await session.scalars(select(SilenceAlertRow)))
        assert alert.local_day.isoformat() == "2026-10-02"
        assert alert.escalated_at == first + timedelta(minutes=120)


async def test_before_deadline_does_not_create_yesterday_initial_alert(sessionmaker, linked_ward):
    await SilenceScheduler(sessionmaker).tick(NOW - timedelta(minutes=1))
    async with sessionmaker() as session:
        assert list(await session.scalars(select(SilenceAlertRow))) == []
