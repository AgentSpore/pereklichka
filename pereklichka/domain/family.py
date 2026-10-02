import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar, Self
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Family:
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class Ward:
    """The elderly person who checks in through the speaker."""

    family_id: UUID
    name: str
    checkin_hour: int
    timezone: str
    escalation_minutes: int = 30
    device_id: str | None = None
    consent_at: datetime | None = None
    consent_version: str | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class Relative:
    family_id: UUID
    chat_id: int
    alert_order: int
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class LinkCode:
    """One-time code a relative reads out to bind a speaker to a ward."""

    TTL: ClassVar[timedelta] = timedelta(minutes=15)
    MAX_FAILED_ATTEMPTS: ClassVar[int] = 5
    MAX_FAILED_ATTEMPTS_TOTAL: ClassVar[int] = 300

    ward_id: UUID
    code: str
    expires_at: datetime
    used_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    @classmethod
    def issue(cls, ward_id: UUID, now: datetime) -> Self:
        return cls(
            ward_id=ward_id, code=f"{secrets.randbelow(10**6):06d}", expires_at=now + cls.TTL
        )
