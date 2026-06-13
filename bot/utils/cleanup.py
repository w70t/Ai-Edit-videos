"""
Temp-file housekeeping.

Two layers of safety:
  1. Per-job directories are deleted as soon as a job is fully done.
  2. A periodic sweeper removes anything older than TTL that slipped through
     (e.g. the bot was killed mid-job). This keeps the Pi's /tmp from filling up.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Files/dirs in WORK_DIR older than this are considered abandoned.
TTL_SECONDS = 2 * 60 * 60          # 2 hours
SWEEP_INTERVAL_SECONDS = 30 * 60   # run every 30 minutes


def remove_path(path: str | Path) -> None:
    """Best-effort delete of a file or directory tree."""
    p = Path(path)
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)
    except Exception as exc:  # never let cleanup crash a request
        log.warning("cleanup failed for %s: %s", p, exc)


async def periodic_sweep(work_dir: Path) -> None:
    """Long-running background task; cancel it on shutdown."""
    while True:
        try:
            now = time.time()
            for child in work_dir.iterdir():
                try:
                    if now - child.stat().st_mtime > TTL_SECONDS:
                        remove_path(child)
                        log.info("swept stale temp: %s", child)
                except FileNotFoundError:
                    pass
        except Exception as exc:
            log.warning("sweep error: %s", exc)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
