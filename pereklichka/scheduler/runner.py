import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.db.alerts import AlertRepository
from pereklichka.scheduler.service import SilenceService


class SilenceScheduler:
    """Owns the periodic job and waits for active database transactions on shutdown."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._running: set[asyncio.Task] = set()

    def start(self) -> None:
        """Start minute sweeps, including a catch-up sweep immediately after startup."""
        self._scheduler.add_job(
            self._scheduled_tick,
            "interval",
            minutes=1,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC),
        )
        self._scheduler.start()

    async def stop(self) -> None:
        """Stop scheduling and finish current sweeps before the engine is disposed."""
        if not self._scheduler.running:
            return
        self._scheduler.pause()
        self._scheduler.remove_all_jobs()
        await asyncio.sleep(0)
        for task in tuple(self._running):
            await task
        self._scheduler.shutdown(wait=False)
        await asyncio.sleep(0)

    async def tick(self, now: datetime | None = None) -> None:
        """Sweep bounded atomic batches using one deterministic timestamp."""
        instant = now or datetime.now(UTC)
        after = None
        while True:
            async with self._sessions() as session, session.begin():
                after = await SilenceService(AlertRepository(session)).sweep(instant, after)
            if after is None:
                return

    async def _scheduled_tick(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("scheduler job requires an asyncio task")
        self._running.add(task)
        try:
            await self.tick()
        except Exception:
            logger.exception("Silence scheduler sweep failed")
        finally:
            self._running.discard(task)
