import os
import subprocess
from pathlib import Path

from sqlalchemy import URL


def configure_environment() -> None:
    for name in ("DATABASE_URL", "BOT_TOKEN", "SKILL_ID"):
        if path := os.environ.get(f"{name}_FILE"):
            os.environ[name] = Path(path).read_text().strip()
    if not os.environ.get("DATABASE_URL"):
        password = Path(os.environ["POSTGRES_PASSWORD_FILE"]).read_text().strip()
        os.environ["DATABASE_URL"] = URL.create(
            "postgresql+asyncpg",
            username="pereklichka",
            password=password,
            host=os.environ.get("DATABASE_HOST", "db"),
            database="pereklichka",
        ).render_as_string(hide_password=False)


if __name__ == "__main__":
    configure_environment()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    os.execvp("python", ["python", "-m", "pereklichka"])
