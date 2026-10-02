import asyncio
import os
import urllib.request

import asyncpg
from sqlalchemy.engine import make_url
from start import configure_environment


async def check_database() -> None:
    url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
    connection = await asyncpg.connect(url.render_as_string(hide_password=False), timeout=3)
    try:
        await connection.fetchval("SELECT 1", timeout=3)
    finally:
        await connection.close(timeout=1)


if __name__ == "__main__":
    try:
        configure_environment()
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("API unavailable")
        asyncio.run(check_database())
    except Exception:
        raise SystemExit(1) from None
