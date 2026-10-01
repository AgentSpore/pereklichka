from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.repositories import (
    CheckInRepository,
    FamilyRepository,
    LinkCodeRepository,
    WardRepository,
)
from pereklichka.domain.checkin import CheckIn
from pereklichka.domain.family import Family, LinkCode, Ward

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


async def _seed_ward(session: AsyncSession) -> Ward:
    family = Family()
    await FamilyRepository(session).add(family)
    ward = Ward(family_id=family.id, name="Анна", checkin_hour=9, timezone="Europe/Moscow")
    await WardRepository(session).add(ward)
    return ward


async def test_link_device_then_find_ward_by_device(session: AsyncSession) -> None:
    ward = await _seed_ward(session)
    wards = WardRepository(session)

    assert await wards.by_device("dev-1") is None
    await wards.link_device(ward.id, "dev-1", NOW)
    await session.commit()

    found = await wards.by_device("dev-1")
    assert found is not None
    assert (found.id, found.device_id, found.consent_at) == (ward.id, "dev-1", NOW)


async def test_code_is_consumed_once_and_not_after_expiry(session: AsyncSession) -> None:
    ward = await _seed_ward(session)
    codes = LinkCodeRepository(session)
    fresh = LinkCode.issue(ward.id, NOW)
    stale = LinkCode(ward_id=ward.id, code="111111", expires_at=NOW - timedelta(seconds=1))
    await codes.add(fresh)
    await codes.add(stale)

    assert await codes.find_active(stale.code, NOW) is None
    assert await codes.consume(stale.code, NOW) is None
    assert await codes.find_active(fresh.code, NOW) == fresh
    assert await codes.consume(fresh.code, NOW) == ward.id
    assert await codes.consume(fresh.code, NOW) is None
    assert await codes.find_active(fresh.code, NOW) is None


async def test_checkin_is_stored(session: AsyncSession) -> None:
    ward = await _seed_ward(session)
    checkin = CheckIn(ward_id=ward.id, at=NOW, wellbeing="хорошо", meds_taken=None, needs=None)
    checkins = CheckInRepository(session)

    await checkins.add(checkin)
    await session.commit()

    assert await checkins.for_ward(ward.id) == [checkin]
