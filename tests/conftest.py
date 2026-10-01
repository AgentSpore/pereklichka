from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from pereklichka.db.session import create_sessionmaker

TEST_LABEL = {"pereklichka.tests": "1"}


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    container = PostgresContainer("postgres:16-alpine", driver="asyncpg").with_kwargs(
        labels=TEST_LABEL
    )
    with container:
        url = container.get_connection_url()
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    maker = create_sessionmaker(database_url)
    async with maker() as session:
        await session.execute(text("TRUNCATE families, link_attempts CASCADE"))
        await session.commit()
    yield maker
    await maker.kw["bind"].dispose()


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
