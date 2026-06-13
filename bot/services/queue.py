"""
A tiny, dependable async job queue.

Why not Celery/RQ? On a single Raspberry Pi they are heavy overkill. A bounded
asyncio.Queue plus a fixed pool of worker tasks gives us exactly what we need:
  * at most N videos processed at once (N = MAX_CONCURRENT_JOBS),
  * back-pressure / FIFO ordering,
  * trivial memory footprint,
  * graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# A job is just an async callable that does the heavy lifting.
JobFn = Callable[[], Awaitable[None]]


@dataclass
class Job:
    id: str
    coro: JobFn
    on_error: Callable[[Exception], Awaitable[None]] | None = None


@dataclass
class JobQueue:
    concurrency: int = 1
    _queue: asyncio.Queue[Job] = field(default_factory=asyncio.Queue)
    _workers: list[asyncio.Task] = field(default_factory=list)
    _running: bool = False

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def submit(self, job: Job) -> int:
        """Enqueue a job; returns its position in line (0 = next up)."""
        position = self._queue.qsize()
        await self._queue.put(job)
        log.info("queued job %s (position %d)", job.id, position)
        return position

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"job-worker-{i}")
            for i in range(max(1, self.concurrency))
        ]
        log.info("job queue started with %d worker(s)", len(self._workers))

    async def _worker(self, idx: int) -> None:
        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                log.info("worker %d running job %s", idx, job.id)
                await job.coro()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad job must not kill the worker
                log.exception("job %s failed: %s", job.id, exc)
                if job.on_error:
                    try:
                        await job.on_error(exc)
                    except Exception:
                        log.exception("error handler for %s also failed", job.id)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        log.info("job queue stopped")
