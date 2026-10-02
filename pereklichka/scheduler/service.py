from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from pereklichka.db.alerts import AlertRepository, SilenceAlertRow
from pereklichka.db.models import WardRow
from pereklichka.domain.deadline import deadline_day
from pereklichka.scheduler.texts import ESCALATION_ALERT, FIRST_ALERT


class SilenceService:
    """Schedules once per local day while repository locks guard the completion check."""

    def __init__(self, alerts: AlertRepository) -> None:
        self._alerts = alerts

    async def sweep(self, now: datetime, after: UUID | None = None) -> UUID | None:
        """Queue one bounded batch; caller commits before requesting the next page."""
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)
        wards = await self._alerts.wards(after)
        if not wards:
            return None
        ward_ids = [ward.id for ward in wards]
        completed = await self._alerts.completions(ward_ids, now)
        existing = await self._alerts.alerts(ward_ids, now)
        recipients = await self._alerts.recipients(list({ward.family_id for ward in wards}))
        for ward in wards:
            local_now = now.astimezone(ZoneInfo(ward.timezone))
            day = deadline_day(now, ward.checkin_hour, ward.timezone)
            times = completed.get(ward.id, [])
            done = any(at.astimezone(local_now.tzinfo).date() >= day for at in times)
            if done or ward.device_id is None or ward.consent_at is None:
                await self._alerts.cancel(ward.id, day)
                continue
            alert = existing.get((ward.id, day))
            if day != local_now.date() and alert is None:
                continue
            await self._queue_due(ward, local_now, alert, recipients.get(ward.family_id, []))
        return wards[-1].id

    async def _queue_due(
        self, ward: WardRow, local_now: datetime, alert: SilenceAlertRow | None, chat_ids: list[int]
    ) -> None:
        deadline = local_now.replace(
            hour=ward.checkin_hour, minute=0, second=0, microsecond=0, fold=0
        )
        now = local_now.astimezone(UTC)
        if not chat_ids or (alert is None and now < deadline.astimezone(UTC)):
            return
        if alert is None:
            await self._alerts.first(
                ward, local_now.date(), now, (FIRST_ALERT.format(name=ward.name), chat_ids[:1])
            )
        elif alert.escalated_at is None and now >= alert.first_at + timedelta(
            minutes=ward.escalation_minutes
        ):
            await self._alerts.escalate(
                alert, now, (ESCALATION_ALERT.format(name=ward.name), chat_ids)
            )
