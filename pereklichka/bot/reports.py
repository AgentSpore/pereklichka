from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.bot import texts
from pereklichka.db.outbox import OutboxRepository
from pereklichka.db.relatives import RelativeRepository
from pereklichka.db.repositories import CheckInRepository, WardRepository
from pereklichka.domain.family import Ward


class ReportService:
    """Completes answers and queues reports in the caller's transaction."""

    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self._checkins = CheckInRepository(session)
        self._wards = WardRepository(session)
        self._relatives = RelativeRepository(session)
        self._outbox = OutboxRepository(session)
        self._now = now

    async def complete(self, checkin_id: UUID, ward: Ward, needs: str | None) -> None:
        await self._wards.lock(ward.id)
        checkin = await self._checkins.complete(checkin_id, ward.id, needs, self._now)
        if checkin is None:
            return
        await self._outbox.cancel_pending(f"silence:{ward.id}:")
        relatives = await self._relatives.members(ward.family_id)
        text = texts.REPORT.format(
            name=ward.name,
            time=self._now.astimezone(ZoneInfo(ward.timezone)).strftime("%d.%m %H:%M"),
            wellbeing=checkin.wellbeing,
            meds=texts.MEDS[checkin.meds_taken],
            needs=checkin.needs or texts.NOTHING,
        )
        await self._outbox.enqueue(f"checkin:{checkin.id}", text, [r.chat_id for r in relatives])
