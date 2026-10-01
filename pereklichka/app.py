from fastapi import FastAPI

from pereklichka.alice.router import router as alice_router
from pereklichka.db.session import create_sessionmaker


def create_app(database_url: str) -> FastAPI:
    app = FastAPI(title="Перекличка")
    app.state.sessionmaker = create_sessionmaker(database_url)
    app.include_router(alice_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
