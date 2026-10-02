from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import InlineKeyboardMarkup
from pydantic import BaseModel, Field, field_validator


class WardInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    checkin_hour: int = Field(ge=0, le=23)
    timezone: str = Field(min_length=1, max_length=64)
    escalation_minutes: int = Field(default=30, ge=1, le=1440)

    @field_validator("name")
    @classmethod
    def name_is_printable(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("Invalid name")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Invalid timezone") from error
        return value


class ScheduleInput(WardInput):
    ward_id: UUID
    name: str = "schedule"


class DraftInput(WardInput):
    """A complete validated draft crosses the conversation/service boundary."""

    ward_id: UUID | None = None


@dataclass(frozen=True)
class Screen:
    text: str
    markup: InlineKeyboardMarkup
    clear_draft: bool = False
