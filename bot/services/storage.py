"""
In-memory job registry.

Each processed item gets a short job_id which is embedded in the inline-keyboard
callback_data. When a button is pressed we look the job up here to find the
source file, the last rendered output, the chosen options, timings, etc.

This is intentionally in-memory: on a single-admin Pi bot, persistence across
restarts is unnecessary and would only add I/O. Old jobs are pruned so memory
stays flat during long 24/7 runs.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .editor import EditOptions, preset_options
from .quality import STANDARD


@dataclass
class JobRecord:
    id: str
    work_dir: Path                       # per-job temp directory
    source: Path | None = None           # downloaded / uploaded original
    source_hash: str = ""                # SHA-256 of the source (duplicate check)
    output: Path | None = None           # latest rendered file
    intensity: str = "medium"
    options: EditOptions = field(default_factory=EditOptions)
    quality: str = STANDARD              # tier used to fetch AND to encode
    origin: str = ""                     # the link, or "upload"
    chat_id: int = 0
    user_id: int = 0                     # who owns this job (button ownership)
    result_message_id: int | None = None # message that holds the keyboard
    # processing metadata for the "Show Processing Info" button
    created: float = field(default_factory=time.time)
    last_render_seconds: float = 0.0
    probe: dict = field(default_factory=dict)
    variant_count: int = 0
    # What the last render actually did, in the user's words.
    edit_notes: list[str] = field(default_factory=list)

    def apply_preset(self, intensity: str) -> None:
        """Switch to a preset — sets both the intensity and its option set."""
        self.intensity = intensity
        self.options = preset_options(intensity)

    @property
    def from_link(self) -> bool:
        """
        True when the source came from a URL, so it can be re-fetched at a
        different quality. A Telegram upload cannot: the bot only ever sees
        the copy Telegram already compressed.
        """
        return self.origin.startswith(("http://", "https://"))


class JobStore:
    """Tiny dict-backed store with size-bounded pruning."""

    def __init__(self, max_jobs: int = 50) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._max = max_jobs

    def new(self, base_dir: Path, chat_id: int, origin: str,
            user_id: int = 0, quality: str = STANDARD) -> JobRecord:
        """Mint a job id, create its private temp dir under `base_dir`."""
        job_id = uuid.uuid4().hex[:10]
        work_dir = base_dir / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        rec = JobRecord(id=job_id, work_dir=work_dir, chat_id=chat_id,
                        origin=origin, user_id=user_id, quality=quality)
        self._jobs[job_id] = rec
        self._prune()
        return rec

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def drop(self, job_id: str) -> JobRecord | None:
        return self._jobs.pop(job_id, None)

    def _prune(self) -> None:
        if len(self._jobs) <= self._max:
            return
        # Remove oldest jobs first.
        for jid in sorted(self._jobs, key=lambda k: self._jobs[k].created)[
            : len(self._jobs) - self._max
        ]:
            self._jobs.pop(jid, None)


store = JobStore()
