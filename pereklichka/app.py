from fastapi import FastAPI

from pereklichka.alice.router import router as alice_router
from pereklichka.bot.runtime import lifespan
from pereklichka.config import Settings
from pereklichka.db.session import create_sessionmaker


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Перекличка", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessionmaker = create_sessionmaker(settings.database_url)
    app.include_router(alice_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
