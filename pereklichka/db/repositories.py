from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.models import CheckInRow, FamilyRow, LinkAttemptRow, LinkCodeRow, WardRow
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

    async def lock(self, ward_id: UUID) -> None:
        await self._session.scalar(
            select(WardRow.id).where(WardRow.id == ward_id).with_for_update()
        )

    async def by_device(self, device_id: str) -> Ward | None:
        row = await self._session.scalar(select(WardRow).where(WardRow.device_id == device_id))
        return None if row is None else self._to_ward(row)

    async def link_device(
        self, ward_id: UUID, device_id: str, consent_at: datetime, consent_version: str
    ) -> None:
        await self._session.execute(
            update(WardRow)
            .where(WardRow.id == ward_id)
            .values(device_id=device_id, consent_at=consent_at, consent_version=consent_version)
        )

    @staticmethod
    def _to_ward(row: WardRow) -> Ward:
        return Ward(
            id=row.id,
            family_id=row.family_id,
            name=row.name,
            checkin_hour=row.checkin_hour,
            timezone=row.timezone,
            escalation_minutes=row.escalation_minutes,
            device_id=row.device_id,
            consent_at=row.consent_at,
            consent_version=row.consent_version,
        )


class LinkCodeRepository:
    ISSUE_ATTEMPTS = 5

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(self, ward_id: UUID, now: datetime) -> LinkCode:
        """New code for the ward; expired unused codes are dropped so their digits free up."""
        await self._session.execute(
            delete(LinkCodeRow).where(LinkCodeRow.used_at.is_(None), LinkCodeRow.expires_at <= now)
        )
        for _ in range(self.ISSUE_ATTEMPTS - 1):
            try:
                return await self._insert(ward_id, now)
            except IntegrityError:
                continue
        return await self._insert(ward_id, now)

    async def revoke_active(self, now: datetime) -> int:
        revoked = await self._session.scalars(
            delete(LinkCodeRow)
            .where(LinkCodeRow.used_at.is_(None), LinkCodeRow.expires_at > now)
            .returning(LinkCodeRow.id)
        )
        return len(revoked.all())

    async def _insert(self, ward_id: UUID, now: datetime) -> LinkCode:
        link_code = LinkCode.issue(ward_id, now)
        async with self._session.begin_nested():
            self._session.add(LinkCodeRow(**vars(link_code)))
        return link_code

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


class LinkAttemptRepository:
    # Any constant works: one transaction-scoped lock serialises every link-code check.
    LOCK_KEY = 0x6C696E6B

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock(self) -> None:
        """Hold until commit, so concurrent guesses cannot all pass the count before inserting."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": self.LOCK_KEY}
        )

    async def add_failure(self, application_id: str, at: datetime) -> None:
        await self._session.execute(
            delete(LinkAttemptRow).where(LinkAttemptRow.at <= at - LinkCode.TTL)
        )
        self._session.add(LinkAttemptRow(application_id=application_id, at=at))
        await self._session.flush()

    async def failures_since(self, application_id: str | None, since: datetime) -> int:
        """Failures after `since` for one speaker, or for all speakers when None."""
        query = select(func.count()).where(LinkAttemptRow.at > since)
        if application_id is not None:
            query = query.where(LinkAttemptRow.application_id == application_id)
        return await self._session.scalar(query) or 0


class CheckInNotFoundError(LookupError):
    """The session points at a check-in this ward does not own; the answer must not be lost."""


class CheckInRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, checkin: CheckIn) -> None:
        self._session.add(CheckInRow(**vars(checkin)))
        await self._session.flush()

    async def set_meds(self, checkin_id: UUID, ward_id: UUID, meds_taken: bool | None) -> None:
        await self._answer(checkin_id, ward_id, meds_taken=meds_taken)

    async def set_needs(self, checkin_id: UUID, ward_id: UUID, needs: str | None) -> None:
        await self._answer(checkin_id, ward_id, needs=needs)

    async def complete(
        self, checkin_id: UUID, ward_id: UUID, needs: str | None, now: datetime
    ) -> CheckIn | None:
        row = await self._session.scalar(
            update(CheckInRow)
            .where(
                CheckInRow.id == checkin_id,
                CheckInRow.ward_id == ward_id,
                CheckInRow.completed_at.is_(None),
            )
            .values(needs=needs, completed_at=now)
            .returning(CheckInRow)
        )
        if row is not None:
            return CheckIn(**{key: getattr(row, key) for key in CheckIn.__dataclass_fields__})
        existing = await self._session.scalar(
            select(CheckInRow.id).where(CheckInRow.id == checkin_id, CheckInRow.ward_id == ward_id)
        )
        if existing is None:
            raise CheckInNotFoundError(checkin_id)
        return None

    async def _answer(self, checkin_id: UUID, ward_id: UUID, **values: object) -> None:
        updated = await self._session.scalar(
            update(CheckInRow)
            .where(CheckInRow.id == checkin_id, CheckInRow.ward_id == ward_id)
            .values(**values)
            .returning(CheckInRow.id)
        )
        if updated is None:
            raise CheckInNotFoundError(checkin_id)

    async def for_ward(self, ward_id: UUID) -> list[CheckIn]:
        rows = await self._session.scalars(
            select(CheckInRow).where(CheckInRow.ward_id == ward_id).order_by(CheckInRow.at)
        )
        return [
            CheckIn(
                id=r.id,
                ward_id=r.ward_id,
                at=r.at,
                completed_at=r.completed_at,
                wellbeing=r.wellbeing,
                meds_taken=r.meds_taken,
                needs=r.needs,
            )
            for r in rows
        ]
