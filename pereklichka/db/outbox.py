from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.models import CheckInRow, OutboxRow, SilenceAlertRow, WardRow
from pereklichka.domain.deadline import day_start, deadline_day
from pereklichka.domain.delivery import Delivery


class OutboxRepository:
    """Durable per-recipient deliveries with expiring ownership of claims."""

    LEASE = timedelta(seconds=60)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, event_key: str, text: str, chat_ids: list[int]) -> None:
        if not chat_ids:
            return
        await self._session.execute(
            insert(OutboxRow)
            .values(
                [
                    {"id": uuid4(), "event_key": event_key, "text": text, "chat_id": chat_id}
                    for chat_id in chat_ids
                ]
            )
            .on_conflict_do_nothing(constraint="uq_outbox_event_chat")
        )

    async def cancel_pending(self, prefix: str) -> None:
        await self._session.execute(
            delete(OutboxRow).where(
                OutboxRow.event_key.startswith(prefix, autoescape=True),
                OutboxRow.delivered_at.is_(None),
            )
        )

    async def still_claimed(self, delivery: Delivery) -> bool:
        return (
            await self._session.scalar(
                select(OutboxRow.id).where(
                    OutboxRow.id == delivery.id,
                    OutboxRow.claim_id == delivery.claim_id,
                    OutboxRow.delivered_at.is_(None),
                )
            )
            is not None
        )

    async def discard_if_stale(self, delivery: Delivery) -> bool:
        event_key = await self._session.scalar(
            select(OutboxRow.event_key).where(
                OutboxRow.id == delivery.id, OutboxRow.claim_id == delivery.claim_id
            )
        )
        if event_key is None or not event_key.startswith("silence:"):
            return False
        _, ward_id, event_day, _ = event_key.split(":")
        stale = await self._silence_stale(UUID(ward_id), date.fromisoformat(event_day))
        if stale:
            await self._session.execute(
                delete(OutboxRow).where(
                    OutboxRow.id == delivery.id,
                    OutboxRow.claim_id == delivery.claim_id,
                    OutboxRow.delivered_at.is_(None),
                )
            )
        return stale

    async def _silence_stale(self, ward_id: UUID, event_day: date) -> bool:
        ward = await self._session.get(WardRow, ward_id)
        if ward is None:
            return True
        now = await self.now()
        day = deadline_day(now, ward.checkin_hour, ward.timezone)
        if event_day != day:
            return True
        start = day_start(day, ward.timezone)
        if event_day != now.astimezone(start.tzinfo).date():
            alert = await self._session.get(SilenceAlertRow, (ward_id, day))
            if alert is None:
                return True
        completed = await self._session.scalar(
            select(CheckInRow.id)
            .where(
                CheckInRow.ward_id == ward_id,
                CheckInRow.completed_at >= start,
                CheckInRow.completed_at <= now,
            )
            .limit(1)
        )
        return completed is not None

    async def now(self) -> datetime:
        return (await self._session.execute(select(func.clock_timestamp()))).scalar_one()

    async def claim(self, now: datetime) -> Delivery | None:
        row = await self._session.scalar(
            select(OutboxRow)
            .where(OutboxRow.delivered_at.is_(None), OutboxRow.available_at <= now)
            .order_by(OutboxRow.available_at, OutboxRow.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        claim_id = uuid4()
        row.claim_id = claim_id
        row.available_at = now + self.LEASE
        row.attempts += 1
        await self._session.flush()
        return Delivery(row.id, claim_id, row.chat_id, row.text, row.attempts)

    async def ack(self, delivery: Delivery, now: datetime) -> bool:
        return await self._update_claim(delivery, delivered_at=now, claim_id=None)

    async def retry(self, delivery: Delivery, now: datetime, delay: int) -> bool:
        return await self._update_claim(
            delivery, available_at=now + timedelta(seconds=delay), claim_id=None
        )

    async def _update_claim(self, delivery: Delivery, **values: object) -> bool:
        updated = await self._session.scalar(
            update(OutboxRow)
            .where(
                OutboxRow.id == delivery.id,
                OutboxRow.claim_id == delivery.claim_id,
                OutboxRow.delivered_at.is_(None),
            )
            .values(**values)
            .returning(OutboxRow.id)
        )
        return updated is not None
