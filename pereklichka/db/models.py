from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, String, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: TIMESTAMP(timezone=True)}


class FamilyRow(Base):
    __tablename__ = "families"

    id: Mapped[UUID] = mapped_column(primary_key=True)


class WardRow(Base):
    __tablename__ = "wards"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    checkin_hour: Mapped[int] = mapped_column(SmallInteger)
    timezone: Mapped[str] = mapped_column(String(64))
    escalation_minutes: Mapped[int] = mapped_column(SmallInteger, server_default="30")
    device_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    consent_at: Mapped[datetime | None]
    consent_version: Mapped[str | None] = mapped_column(String(64))


class RelativeRow(Base):
    __tablename__ = "relatives"
    __table_args__ = (UniqueConstraint("chat_id", name="uq_relatives_chat_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    alert_order: Mapped[int] = mapped_column(SmallInteger)


class LinkCodeRow(Base):
    __tablename__ = "link_codes"
    __table_args__ = (
        Index(
            "uq_link_codes_unused_code",
            "code",
            unique=True,
            postgresql_where=sql_text("used_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    ward_id: Mapped[UUID] = mapped_column(ForeignKey("wards.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(6))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class LinkAttemptRow(Base):
    """A failed link-code attempt, counted to stop code guessing from one speaker."""

    __tablename__ = "link_attempts"
    __table_args__ = (Index("ix_link_attempts_application_at", "application_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime]


class CheckInRow(Base):
    __tablename__ = "check_ins"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    ward_id: Mapped[UUID] = mapped_column(ForeignKey("wards.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime]
    wellbeing: Mapped[str] = mapped_column(String(1024))
    meds_taken: Mapped[bool | None]
    needs: Mapped[str | None] = mapped_column(String(1024))
    completed_at: Mapped[datetime | None]


class InvitationRow(Base):
    __tablename__ = "invitations"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime]


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("event_key", "chat_id", name="uq_outbox_event_chat"),
        Index(
            "ix_outbox_pending", "available_at", postgresql_where=sql_text("delivered_at IS NULL")
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(150))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(String(4096))
    available_at: Mapped[datetime] = mapped_column(server_default=sql_text("now()"))
    delivered_at: Mapped[datetime | None]
    claim_id: Mapped[UUID | None]
    attempts: Mapped[int] = mapped_column(server_default="0")
