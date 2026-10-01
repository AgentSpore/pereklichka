import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from pereklichka.config import DatabaseSettings
from pereklichka.db.models import Base


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or DatabaseSettings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


asyncio.run(_run())
