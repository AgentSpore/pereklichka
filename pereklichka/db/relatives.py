from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.models import FamilyRow, InvitationRow, RelativeRow, WardRow
from pereklichka.db.repositories import WardRepository
from pereklichka.domain.family import Relative, Ward


class RelativeRepository:
    """Family membership is the boundary for every bot action."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_chat(self, chat_id: int) -> None:
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": chat_id})

    async def family_for(self, chat_id: int) -> UUID | None:
        return await self._session.scalar(
            select(RelativeRow.family_id).where(RelativeRow.chat_id == chat_id)
        )

    async def create_family(self, chat_id: int) -> UUID:
        await self.lock_chat(chat_id)
        family_id = await self.family_for(chat_id)
        if family_id is not None:
            return family_id
        family_id = uuid4()
        self._session.add(FamilyRow(id=family_id))
        await self._session.flush()
        await self.add_member(family_id, chat_id)
        return family_id

    async def add_member(self, family_id: UUID, chat_id: int) -> None:
        await self._session.scalar(
            select(FamilyRow.id).where(FamilyRow.id == family_id).with_for_update()
        )
        order = await self._session.scalar(
            select(func.max(RelativeRow.alert_order)).where(RelativeRow.family_id == family_id)
        )
        self._session.add(
            RelativeRow(
                id=uuid4(), family_id=family_id, chat_id=chat_id, alert_order=(order or 0) + 1
            )
        )
        await self._session.flush()

    async def members(self, family_id: UUID) -> list[Relative]:
        rows = await self._session.scalars(
            select(RelativeRow)
            .where(RelativeRow.family_id == family_id)
            .order_by(RelativeRow.alert_order, RelativeRow.id)
        )
        return [
            Relative(id=r.id, family_id=r.family_id, chat_id=r.chat_id, alert_order=r.alert_order)
            for r in rows
        ]

    async def set_order(self, family_id: UUID, chat_ids: list[int]) -> bool:
        await self._session.scalar(
            select(FamilyRow.id).where(FamilyRow.id == family_id).with_for_update()
        )
        members = await self.members(family_id)
        if len(chat_ids) != len(members) or set(chat_ids) != {r.chat_id for r in members}:
            return False
        for order, chat_id in enumerate(chat_ids, 1):
            await self._session.execute(
                update(RelativeRow)
                .where(RelativeRow.family_id == family_id, RelativeRow.chat_id == chat_id)
                .values(alert_order=order)
            )
        return True

    async def wards(self, family_id: UUID) -> list[Ward]:
        rows = await self._session.scalars(
            select(WardRow).where(WardRow.family_id == family_id).order_by(WardRow.name, WardRow.id)
        )
        return [WardRepository._to_ward(row) for row in rows]

    async def ward(self, family_id: UUID, ward_id: UUID) -> Ward | None:
        row = await self._session.scalar(
            select(WardRow).where(WardRow.id == ward_id, WardRow.family_id == family_id)
        )
        return None if row is None else WardRepository._to_ward(row)

    async def schedule(self, ward: Ward) -> None:
        await self._session.execute(
            update(WardRow)
            .where(WardRow.id == ward.id, WardRow.family_id == ward.family_id)
            .values(
                checkin_hour=ward.checkin_hour,
                timezone=ward.timezone,
                escalation_minutes=ward.escalation_minutes,
            )
        )

    async def invite(self, token_hash: str, family_id: UUID, expires_at: datetime) -> None:
        self._session.add(
            InvitationRow(token_hash=token_hash, family_id=family_id, expires_at=expires_at)
        )
        await self._session.flush()

    async def invitation(self, token_hash: str, now: datetime) -> UUID | None:
        return await self._session.scalar(
            select(InvitationRow.family_id).where(
                InvitationRow.token_hash == token_hash, InvitationRow.expires_at > now
            )
        )
