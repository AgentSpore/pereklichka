from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Dialogs drops the turn after 4.5 s; a stuck database must fail well before that.
DB_TIMEOUT_SECONDS = 2


def create_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"timeout": DB_TIMEOUT_SECONDS, "command_timeout": DB_TIMEOUT_SECONDS},
    )
    return async_sessionmaker(engine, expire_on_commit=False)
