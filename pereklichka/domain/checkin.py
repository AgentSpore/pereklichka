from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(frozen=True)
class CheckIn:
    ward_id: UUID
    at: datetime
    wellbeing: str
    meds_taken: bool | None
    needs: str | None
    completed_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
