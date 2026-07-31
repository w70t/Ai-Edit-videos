"""
Who may use the bot, and who may ask it for high quality.

Two tiers, both managed entirely from buttons:

  * **allowed**  – the bot answers this person at all. Everyone here gets
    1080p, which is the whole point: the Pi stays responsive for the group.
  * **hq**       – this person may also pick 1440p / 4K / best. The admin
    grants it per person, because every grant is a licence to occupy the
    single render slot for a long time.

The admin (ADMIN_ID) is implicit in both tiers and is never stored — the file
can be deleted and the owner still gets in.

Pending requests
----------------
Anyone who messages the bot without a grant lands in `pending` and the admin
gets an approve/deny card. Keeping them in the same file means a restart does
not lose a request that arrived overnight. The list is capped: an unapproved
stranger must never be able to grow this file without bound.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from ..config import settings
from .quality import STANDARD, get as get_tier

log = logging.getLogger(__name__)

# Hard ceiling on queued join requests — spam protection, oldest dropped first.
MAX_PENDING = 50


@dataclass
class UserEntry:
    id: int
    name: str
    hq: bool = False
    quality: str = STANDARD     # remembered choice, reused for their next video
    added: float = 0.0

    @property
    def tier_label(self) -> str:
        return "⭐ جودة عالية" if self.hq else "1080p"


class UserRegistry:
    """JSON-file-backed allowlist. Small enough that every write is a full dump."""

    def __init__(self, path, admin_id: int) -> None:
        self._path = path
        self._admin = admin_id
        self._users: dict[int, UserEntry] = {}
        self._pending: dict[int, dict] = {}
        self._load()

    # --- persistence ------------------------------------------------------- #
    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except (ValueError, OSError) as exc:
            log.warning("could not read user registry %s: %s", self._path, exc)
            return
        if not isinstance(raw, dict):
            return
        for uid, rec in (raw.get("users") or {}).items():
            try:
                self._users[int(uid)] = UserEntry(
                    id=int(uid),
                    name=str(rec.get("name", "")),
                    hq=bool(rec.get("hq", False)),
                    # A tier that no longer exists must not resurrect as itself.
                    quality=get_tier(rec.get("quality", STANDARD)).key,
                    added=float(rec.get("added", 0) or 0),
                )
            except (TypeError, ValueError):
                continue
        for uid, rec in (raw.get("pending") or {}).items():
            try:
                self._pending[int(uid)] = {
                    "name": str(rec.get("name", "")),
                    "at": float(rec.get("at", 0) or 0),
                }
            except (TypeError, ValueError):
                continue
        log.info("user registry: %d allowed, %d pending",
                 len(self._users), len(self._pending))

    def _save(self) -> None:
        payload = {
            "users": {
                str(u.id): {"name": u.name, "hq": u.hq,
                            "quality": u.quality, "added": u.added}
                for u in self._users.values()
            },
            "pending": {str(k): v for k, v in self._pending.items()},
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            tmp.replace(self._path)      # atomic on the same filesystem
        except OSError as exc:
            log.warning("could not write user registry %s: %s", self._path, exc)

    # --- queries ----------------------------------------------------------- #
    def is_admin(self, uid: int) -> bool:
        return uid == self._admin

    def allowed(self, uid: int) -> bool:
        """May this user talk to the bot at all?"""
        return self.is_admin(uid) or uid in self._users

    def can_hq(self, uid: int) -> bool:
        """May this user pick a tier above 1080p?"""
        if self.is_admin(uid):
            return True
        entry = self._users.get(uid)
        return bool(entry and entry.hq)

    def get(self, uid: int) -> UserEntry | None:
        return self._users.get(uid)

    def users(self) -> list[UserEntry]:
        """Allowlisted users, newest grant last."""
        return sorted(self._users.values(), key=lambda u: u.added)

    def count(self) -> int:
        return len(self._users)

    def default_quality(self, uid: int) -> str:
        """
        The tier to start this user's next job at.

        Their remembered pick, but only if they still hold the grant — revoking
        HQ must take effect immediately, not at their next manual change.
        """
        if self.is_admin(uid):
            entry = self._users.get(uid)
            return entry.quality if entry else STANDARD
        entry = self._users.get(uid)
        if entry is None:
            return STANDARD
        tier = get_tier(entry.quality)
        return tier.key if (not tier.gated or entry.hq) else STANDARD

    # --- mutations --------------------------------------------------------- #
    def add(self, uid: int, name: str = "", hq: bool = False) -> UserEntry:
        entry = self._users.get(uid)
        if entry is None:
            entry = UserEntry(id=uid, name=name, hq=hq, added=time.time())
            self._users[uid] = entry
        else:
            entry.hq = hq
            if name:
                entry.name = name
        self._pending.pop(uid, None)
        self._save()
        return entry

    def remove(self, uid: int) -> bool:
        removed = self._users.pop(uid, None) is not None
        if removed:
            self._save()
        return removed

    def set_hq(self, uid: int, on: bool) -> bool:
        entry = self._users.get(uid)
        if entry is None:
            return False
        entry.hq = on
        if not on:
            # Drop a now-forbidden remembered tier so it cannot come back.
            if get_tier(entry.quality).gated:
                entry.quality = STANDARD
        self._save()
        return True

    def set_quality(self, uid: int, key: str) -> None:
        """Remember a user's tier choice for their next video."""
        entry = self._users.get(uid)
        if entry is None:
            if not self.is_admin(uid):
                return
            # The admin is not in the file by default; materialise a row so
            # their preference survives a restart like everyone else's.
            entry = UserEntry(id=uid, name="admin", hq=True, added=time.time())
            self._users[uid] = entry
        entry.quality = get_tier(key).key
        self._save()

    # --- join requests ----------------------------------------------------- #
    def request(self, uid: int, name: str) -> bool:
        """
        Record a join request. Returns True only the FIRST time, so the admin
        is pinged once per stranger no matter how much they message.
        """
        if self.allowed(uid) or uid in self._pending:
            return False
        self._pending[uid] = {"name": name, "at": time.time()}
        if len(self._pending) > MAX_PENDING:
            oldest = min(self._pending, key=lambda k: self._pending[k]["at"])
            self._pending.pop(oldest, None)
        self._save()
        return True

    def pending(self) -> list[tuple[int, str]]:
        """(user_id, display name) for everyone waiting, oldest first."""
        return [(uid, rec.get("name") or str(uid))
                for uid, rec in sorted(self._pending.items(),
                                       key=lambda kv: kv[1].get("at", 0))]

    def pending_name(self, uid: int) -> str:
        rec = self._pending.get(uid)
        return (rec or {}).get("name") or str(uid)

    def deny(self, uid: int) -> bool:
        removed = self._pending.pop(uid, None) is not None
        if removed:
            self._save()
        return removed


# Single shared registry for the whole process.
registry = UserRegistry(settings.users_db, settings.admin_id)
