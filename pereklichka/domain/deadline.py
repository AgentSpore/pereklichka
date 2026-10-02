from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def deadline_day(now: datetime, hour: int, timezone: str) -> date:
    """Return the local date of the most recent deadline, including across midnight."""
    local_now = now.astimezone(ZoneInfo(timezone))
    deadline = local_now.replace(hour=hour, minute=0, second=0, microsecond=0, fold=0)
    if now.astimezone(UTC) < deadline.astimezone(UTC):
        return local_now.date() - timedelta(days=1)
    return local_now.date()


def day_start(day: date, timezone: str) -> datetime:
    """Return the start of a local check-in day as an aware instant."""
    return datetime.combine(day, time.min, ZoneInfo(timezone))
