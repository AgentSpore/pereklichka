import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from pereklichka.domain.deadline import deadline_day
from pereklichka.scheduler.runner import SilenceScheduler
from pereklichka.scheduler.service import SilenceService


async def test_sweep_requires_aware_timestamp() -> None:
    repository = AsyncMock()
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        await SilenceService(repository).sweep(datetime(2026, 10, 2))
    repository.wards.assert_not_called()


@pytest.mark.parametrize(
    ("zone", "hour", "instant", "expected"),
    [
        ("Europe/Moscow", 9, "2026-10-02T05:59:59+00:00", False),
        ("Europe/Moscow", 9, "2026-10-02T06:00:00+00:00", True),
        ("Europe/Berlin", 2, "2026-03-29T00:59:59+00:00", False),
        ("Europe/Berlin", 2, "2026-03-29T01:00:00+00:00", True),
        ("Europe/Berlin", 2, "2026-10-25T00:00:00+00:00", True),
        ("Pacific/Kiritimati", 0, "2026-10-02T10:00:00+00:00", True),
    ],
)
async def test_deadline_uses_local_zone_including_dst(zone, hour, instant, expected) -> None:
    repository = AsyncMock()
    ward = MagicMock()
    ward.checkin_hour = hour
    ward.name = "Анна"
    now = datetime.fromisoformat(instant).astimezone(ZoneInfo(zone))
    await SilenceService(repository)._queue_due(ward, now, None, [10, 20])
    assert repository.first.await_count == int(expected)
    if expected:
        assert repository.first.call_args.args[1] == now.date()
        assert repository.first.call_args.args[3][1] == [10]


async def test_stop_drains_created_job_before_it_enters_callback(monkeypatch) -> None:
    scheduler = SilenceScheduler(MagicMock())
    finished = asyncio.Event()

    async def tick(now=None):
        await asyncio.sleep(0)
        finished.set()

    monkeypatch.setattr(scheduler, "tick", tick)
    scheduler.start()
    pending = asyncio.create_task(scheduler._scheduled_tick())
    try:
        await scheduler.stop()
        assert finished.is_set()
    finally:
        await pending


@pytest.mark.parametrize(
    ("instant", "hour", "expected"),
    [
        ("2026-10-02T22:00:00+00:00", 23, "2026-10-02"),
        ("2026-10-02T22:00:00+00:00", 0, "2026-10-03"),
        ("2026-03-29T00:59:59+00:00", 2, "2026-03-28"),
        ("2026-03-29T01:00:00+00:00", 2, "2026-03-29"),
    ],
)
async def test_deadline_cycle_preserves_midnight_and_dst(instant, hour, expected):
    zone = "Europe/Berlin" if instant.startswith("2026-03") else "Europe/Moscow"
    assert deadline_day(datetime.fromisoformat(instant), hour, zone).isoformat() == expected
