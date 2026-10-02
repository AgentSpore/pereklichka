from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.models import Base, LinkAttemptRow, LinkCodeRow
from pereklichka.db.repositories import (
    CheckInRepository,
    FamilyRepository,
    LinkAttemptRepository,
    LinkCodeRepository,
    WardRepository,
)
from pereklichka.domain import family as family_module
from pereklichka.domain.checkin import CheckIn
from pereklichka.domain.family import Family, Ward

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


@pytest.fixture
async def ward(session: AsyncSession) -> Ward:
    family = Family()
    await FamilyRepository(session).add(family)
    ward = Ward(family_id=family.id, name="Анна", checkin_hour=9, timezone="Europe/Moscow")
    await WardRepository(session).add(ward)
    return ward


def _schema_diff(connection: Connection) -> list:
    return compare_metadata(MigrationContext.configure(connection), Base.metadata)


async def test_migrations_match_models(session: AsyncSession) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_schema_diff) == []


async def test_link_device_then_find_ward_by_device(session: AsyncSession, ward: Ward) -> None:
    wards = WardRepository(session)

    assert await wards.by_device("dev-1") is None
    await wards.link_device(ward.id, "dev-1", NOW, "v1")
    await session.commit()

    found = await wards.by_device("dev-1")
    assert found is not None
    assert (found.id, found.device_id, found.consent_at, found.consent_version) == (
        ward.id,
        "dev-1",
        NOW,
        "v1",
    )


async def test_code_is_consumed_once_and_not_after_expiry(
    session: AsyncSession, ward: Ward
) -> None:
    codes = LinkCodeRepository(session)
    stale = await codes.issue(ward.id, NOW - timedelta(minutes=15, seconds=1))
    fresh = await codes.issue(ward.id, NOW)

    assert await codes.find_active(stale.code, NOW) is None
    assert await codes.consume(stale.code, NOW) is None
    assert await codes.find_active(fresh.code, NOW) == fresh
    assert await codes.consume(fresh.code, NOW) == ward.id
    assert await codes.consume(fresh.code, NOW) is None
    assert await codes.find_active(fresh.code, NOW) is None


async def test_issue_drops_expired_codes_and_retries_a_taken_one(
    session: AsyncSession, ward: Ward, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = iter([111111, 111111, 111111, 222222])
    monkeypatch.setattr(family_module.secrets, "randbelow", lambda _: next(drawn))
    codes = LinkCodeRepository(session)

    expired = await codes.issue(ward.id, NOW - timedelta(hours=1))
    active = await codes.issue(ward.id, NOW)
    retried = await codes.issue(ward.id, NOW)

    assert (expired.code, active.code, retried.code) == ("111111", "111111", "222222")
    assert await session.scalar(select(func.count()).select_from(LinkCodeRow)) == 2


async def test_failed_attempts_are_counted_per_device_in_window(session: AsyncSession) -> None:
    attempts = LinkAttemptRepository(session)
    await attempts.add_failure("dev-1", NOW - timedelta(minutes=20))
    await attempts.add_failure("dev-1", NOW)
    await attempts.add_failure("dev-2", NOW)

    since = NOW - timedelta(minutes=15)
    assert await attempts.failures_since("dev-1", since) == 1
    assert await attempts.failures_since(None, since) == 2
    assert await session.scalar(select(func.count()).select_from(LinkAttemptRow)) == 2


async def test_checkin_is_stored_then_answered(session: AsyncSession, ward: Ward) -> None:
    checkin = CheckIn(ward_id=ward.id, at=NOW, wellbeing="хорошо", meds_taken=None, needs=None)
    checkins = CheckInRepository(session)

    await checkins.add(checkin)
    await checkins.set_meds(checkin.id, ward.id, True)
    await checkins.set_needs(checkin.id, ward.id, "хлеба")
    await session.commit()

    [stored] = await checkins.for_ward(ward.id)
    assert (stored.meds_taken, stored.needs) == (True, "хлеба")
