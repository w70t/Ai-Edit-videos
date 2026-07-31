"""
Persistent duplicate-input registry.

Skips re-processing a source video the bot has already edited before. We hash
the *content* of the source file (SHA-256, streamed in chunks so a large clip
doesn't blow up memory on the Pi) and remember it on disk, so the check
survives restarts of the 24/7 service.

Scope (be honest about it): this matches *identical source bytes*. It reliably
catches the same file re-uploaded / re-forwarded, and the same link sent twice
when the download is byte-identical. It is NOT a perceptual matcher — a clip
re-encoded, trimmed, or downloaded in a different format will hash differently
and be treated as new.

Records are scoped **per user**. Two people are allowed to post the same clip;
one of them being told "already processed" for a video they never sent would be
a bug, not a feature. The user id is folded into the stored key, so the file
format stays a flat dict and pruning keeps working unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024  # 1 MiB read window


def hash_file(path: Path) -> str:
    """Stream a file through SHA-256 and return the hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class DuplicateRegistry:
    """JSON-file-backed set of source hashes, with bounded, pruned size."""

    def __init__(self, path: Path, max_entries: int = 5000) -> None:
        self._path = path
        self._max = max_entries
        self._seen: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, dict):
                self._seen = raw
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            log.warning("could not read dedup registry %s: %s", self._path, exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._seen), "utf-8")
            tmp.replace(self._path)  # atomic on the same filesystem
        except OSError as exc:
            log.warning("could not write dedup registry %s: %s", self._path, exc)

    @staticmethod
    def _key(digest: str, user_id: int) -> str:
        return f"{user_id}:{digest}"

    def seen(self, digest: str, user_id: int) -> dict | None:
        """Return this user's record for a digest, or None if never processed."""
        return self._seen.get(self._key(digest, user_id))

    def count(self) -> int:
        """How many distinct sources have been processed."""
        return len(self._seen)

    def clear(self) -> int:
        """Forget every recorded source. Returns how many were removed."""
        n = len(self._seen)
        self._seen.clear()
        self._save()
        return n

    def remember(self, digest: str, user_id: int, origin: str = "") -> None:
        """Record a digest as processed for this user and persist to disk."""
        if not digest:
            return
        self._seen[self._key(digest, user_id)] = {
            "first_seen": time.time(), "origin": origin, "user": user_id,
        }
        self._prune()
        self._save()

    def _prune(self) -> None:
        if len(self._seen) <= self._max:
            return
        # Drop the oldest records first so memory/disk stay bounded.
        for d in sorted(
            self._seen, key=lambda k: self._seen[k].get("first_seen", 0)
        )[: len(self._seen) - self._max]:
            self._seen.pop(d, None)


# Single shared registry for the whole process.
registry = DuplicateRegistry(settings.dedup_db)
