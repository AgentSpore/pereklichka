from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Date, ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from pereklichka.db.models import Base, CheckInRow, RelativeRow, WardRow
from pereklichka.db.outbox import OutboxRepository


class SilenceAlertRow(Base):
    __tablename__ = "silence_alerts"

    ward_id: Mapped[UUID] = mapped_column(
        ForeignKey("wards.id", ondelete="CASCADE"), primary_key=True
    )
    local_day: Mapped[date] = mapped_column(Date, primary_key=True)
    first_at: Mapped[datetime]
    escalated_at: Mapped[datetime | None]


class AlertRepository:
    """Locks wards before reading completion state and queuing daily alerts."""

    BATCH_SIZE = 100

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = OutboxRepository(session)

    async def wards(self, after: UUID | None) -> list[WardRow]:
        query = select(WardRow).order_by(WardRow.id).limit(self.BATCH_SIZE)
        if after is not None:
            query = query.where(WardRow.id > after)
        rows = await self._session.scalars(query.with_for_update(skip_locked=True))
        return list(rows)

    async def completions(self, ward_ids: list[UUID], now: datetime) -> dict[UUID, list[datetime]]:
        rows = await self._session.execute(
            select(CheckInRow.ward_id, CheckInRow.completed_at).where(
                CheckInRow.ward_id.in_(ward_ids),
                CheckInRow.completed_at >= now - timedelta(days=2),
                CheckInRow.completed_at <= now,
            )
        )
        result: dict[UUID, list[datetime]] = {}
        for ward_id, completed_at in rows:
            result.setdefault(ward_id, []).append(completed_at)
        return result

    async def alerts(
        self, ward_ids: list[UUID], now: datetime
    ) -> dict[tuple[UUID, date], SilenceAlertRow]:
        rows = await self._session.scalars(
            select(SilenceAlertRow).where(
                SilenceAlertRow.ward_id.in_(ward_ids),
                SilenceAlertRow.local_day >= (now - timedelta(days=2)).date(),
            )
        )
        return {(row.ward_id, row.local_day): row for row in rows}

    async def recipients(self, family_ids: list[UUID]) -> dict[UUID, list[int]]:
        rows = await self._session.scalars(
            select(RelativeRow)
            .where(RelativeRow.family_id.in_(family_ids))
            .order_by(RelativeRow.alert_order, RelativeRow.id)
        )
        result: dict[UUID, list[int]] = {}
        for row in rows:
            recipients = result.setdefault(row.family_id, [])
            if row.chat_id not in recipients:
                recipients.append(row.chat_id)
        return result

    async def first(
        self, ward: WardRow, day: date, now: datetime, delivery: tuple[str, list[int]]
    ) -> None:
        self._session.add(SilenceAlertRow(ward_id=ward.id, local_day=day, first_at=now))
        await self._session.flush()
        await self._outbox.enqueue(self.event_key(ward.id, day, "first"), *delivery)

    async def escalate(
        self, alert: SilenceAlertRow, now: datetime, delivery: tuple[str, list[int]]
    ) -> None:
        alert.escalated_at = now
        await self._outbox.enqueue(self.event_key(alert.ward_id, alert.local_day, "all"), *delivery)

    async def cancel(self, ward_id: UUID, day: date) -> None:
        await self._outbox.cancel_pending(f"silence:{ward_id}:{day.isoformat()}:")

    @staticmethod
    def event_key(ward_id: UUID, day: date, stage: str) -> str:
        return f"silence:{ward_id}:{day.isoformat()}:{stage}"
