from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.models import CheckInRow, FamilyRow, LinkCodeRow, WardRow
from pereklichka.domain.checkin import CheckIn
from pereklichka.domain.family import Family, LinkCode, Ward


class FamilyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, family: Family) -> None:
        self._session.add(FamilyRow(id=family.id))
        await self._session.flush()


class WardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, ward: Ward) -> None:
        self._session.add(WardRow(**vars(ward)))
        await self._session.flush()

    async def by_device(self, device_id: str) -> Ward | None:
        row = await self._session.scalar(select(WardRow).where(WardRow.device_id == device_id))
        return None if row is None else self._to_ward(row)

    async def link_device(self, ward_id: UUID, device_id: str, consent_at: datetime) -> None:
        await self._session.execute(
            update(WardRow)
            .where(WardRow.id == ward_id)
            .values(device_id=device_id, consent_at=consent_at)
        )

    @staticmethod
    def _to_ward(row: WardRow) -> Ward:
        return Ward(
            id=row.id,
            family_id=row.family_id,
            name=row.name,
            checkin_hour=row.checkin_hour,
            timezone=row.timezone,
            device_id=row.device_id,
            consent_at=row.consent_at,
        )


class LinkCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, link_code: LinkCode) -> None:
        self._session.add(LinkCodeRow(**vars(link_code)))
        await self._session.flush()

    async def find_active(self, code: str, now: datetime) -> LinkCode | None:
        row = await self._session.scalar(
            select(LinkCodeRow).where(
                LinkCodeRow.code == code,
                LinkCodeRow.used_at.is_(None),
                LinkCodeRow.expires_at > now,
            )
        )
        if row is None:
            return None
        return LinkCode(id=row.id, ward_id=row.ward_id, code=row.code, expires_at=row.expires_at)

    async def consume(self, code: str, now: datetime) -> UUID | None:
        """Mark the code used in one statement; None when it was already used or expired."""
        return await self._session.scalar(
            update(LinkCodeRow)
            .where(
                LinkCodeRow.code == code,
                LinkCodeRow.used_at.is_(None),
                LinkCodeRow.expires_at > now,
            )
            .values(used_at=now)
            .returning(LinkCodeRow.ward_id)
        )


class CheckInRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, checkin: CheckIn) -> None:
        self._session.add(CheckInRow(**vars(checkin)))
        await self._session.flush()

    async def for_ward(self, ward_id: UUID) -> list[CheckIn]:
        rows = await self._session.scalars(
            select(CheckInRow).where(CheckInRow.ward_id == ward_id).order_by(CheckInRow.at)
        )
        return [
            CheckIn(
                id=r.id,
                ward_id=r.ward_id,
                at=r.at,
                wellbeing=r.wellbeing,
                meds_taken=r.meds_taken,
                needs=r.needs,
            )
            for r in rows
        ]
