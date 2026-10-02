from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Delivery:
    id: UUID
    claim_id: UUID
    chat_id: int
    text: str
    attempts: int
