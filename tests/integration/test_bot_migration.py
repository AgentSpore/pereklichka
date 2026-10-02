import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.db.models import CheckInRow, OutboxRow


async def test_upgrade_retains_unknown_history_without_queue(
    database_url: str, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.downgrade, config, "0002")
    now = datetime.now(UTC)
    family_id, ward_id, checkin_id = uuid4(), uuid4(), uuid4()
    try:
        async with sessionmaker() as session, session.begin():
            await session.execute(text("INSERT INTO families (id) VALUES (:id)"), {"id": family_id})
            await session.execute(
                text(
                    "INSERT INTO wards (id, family_id, name, checkin_hour, timezone) "
                    "VALUES (:id, :family, 'Test', 9, 'Europe/Moscow')"
                ),
                {"id": ward_id, "family": family_id},
            )
            await session.execute(
                text(
                    "INSERT INTO check_ins (id, ward_id, at, wellbeing) "
                    "VALUES (:id, :ward, :at, 'fine')"
                ),
                {"id": checkin_id, "ward": ward_id, "at": now},
            )
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with sessionmaker() as session:
        assert await session.scalar(select(CheckInRow.completed_at)) is None
        assert await session.scalar(select(func.count()).select_from(OutboxRow)) == 0
