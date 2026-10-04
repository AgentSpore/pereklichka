"""Explicit synthetic access; each application owns a separate test ward."""

import hashlib
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.config import Settings
from pereklichka.db.moderation import ModerationRepository
from pereklichka.db.repositories import LinkAttemptRepository, WardRepository
from pereklichka.domain.family import MODERATION_PREFIX, Ward

CONSENT = (
    "Это тест модерации. Используйте только вымышленные ответы. "
    "Я спрошу о самочувствии, лекарствах и просьбах. "
    "Ответы получает только отдельная тестовая группа в Телеграм. "
    "Навык не сохраняет аудиозаписи. Вы согласны? Скажите да или нет."
)
VERSION = MODERATION_PREFIX + hashlib.sha256(CONSENT.encode()).hexdigest()[:53]
CLOSED = "Тестовый доступ выключен или недоступен. Попросите владельца обновить условия проверки."


class ModerationAccess:
    def __init__(self, session: AsyncSession, settings: Settings | None, now: datetime) -> None:
        self._session = session
        self._settings = settings
        self._now = now
        self._wards = WardRepository(session)
        self._access = ModerationRepository(session)

    @staticmethod
    def owns(ward: Ward) -> bool:
        return (ward.consent_version or "").startswith(MODERATION_PREFIX)

    async def available(self) -> bool:
        settings = self._settings
        if settings is None or settings.moderation_until is None:
            return False
        if settings.moderation_until <= self._now:
            return False
        if settings.moderation_family_id is None or settings.moderation_recipient_chat_id is None:
            return False
        return await self._access.ready(
            settings.moderation_family_id, settings.moderation_recipient_chat_id
        )

    async def permits(self, ward: Ward) -> bool:
        return (
            self._settings is not None
            and ward.family_id == self._settings.moderation_family_id
            and await self.available()
        )

    async def link(self, application_id: str) -> Ward | None:
        settings = self._settings
        if settings is None or settings.moderation_family_id is None:
            return None
        await LinkAttemptRepository(self._session).lock()
        if not await self.available():
            return None
        existing = await self._wards.by_device(application_id)
        if existing is not None:
            return existing if self.owns(existing) and await self.permits(existing) else None
        ward = Ward(
            family_id=settings.moderation_family_id,
            name="Тест модерации",
            checkin_hour=9,
            timezone="Europe/Moscow",
            device_id=application_id,
            consent_at=self._now,
            consent_version=VERSION,
        )
        await self._wards.add(ward)
        return ward
