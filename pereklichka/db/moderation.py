from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.db.models import FamilyRow, LinkCodeRow, RelativeRow, WardRow
from pereklichka.domain.family import MODERATION_CODE, MODERATION_PREFIX


class ModerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ready(self, family_id: UUID, recipient: int) -> bool:
        family = await self._session.scalar(
            select(FamilyRow.id).where(FamilyRow.id == family_id).with_for_update()
        )
        if family is None:
            return False
        recipients = list(
            await self._session.scalars(
                select(RelativeRow.chat_id).where(RelativeRow.family_id == family_id)
            )
        )
        collision = await self._session.scalar(
            select(LinkCodeRow.id)
            .where(LinkCodeRow.code == MODERATION_CODE, LinkCodeRow.used_at.is_(None))
            .limit(1)
        )
        ordinary = await self._session.scalar(
            select(WardRow.id)
            .where(
                WardRow.family_id == family_id,
                or_(
                    WardRow.consent_version.is_(None),
                    WardRow.consent_version.not_like(MODERATION_PREFIX + "%"),
                ),
            )
            .limit(1)
        )
        return recipients == [recipient] and collision is None and ordinary is None
