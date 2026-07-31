"""
Runtime preferences — the switches the admin flips from inside Telegram.

`config.settings` stays what it always was: boot-time configuration read from
.env (token, admin id, paths, hard limits). The handful of switches here are
different in kind. They are day-to-day decisions, and making the admin SSH into
the Pi, edit `.env` and restart the service just to stop the bot asking before
every edit is the wrong trade.

So they work like this:
  * the values in `.env` remain the DEFAULTS (nothing about an existing install
    changes),
  * whatever the admin last chose in the ⚙️ الإعدادات panel overrides them,
  * every change is written to PREFS_DB straight away, so it survives a restart
    of the 24/7 service.

Only these three are runtime-editable on purpose. Token, admin id, paths and
concurrency change how the process is wired at startup; a button that pretends
to change them would be a lie until the next restart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from ..config import INTENSITIES, settings

log = logging.getLogger(__name__)

# The fields the ⚙️ panel may flip. Anything not listed here is boot-time only.
BOOL_FIELDS = ("confirm_before_edit", "skip_duplicates")


@dataclass
class Prefs:
    """Admin-editable settings. Field names double as the storage keys."""

    confirm_before_edit: bool
    default_intensity: str
    skip_duplicates: bool

    # --- mutation (each write hits the disk immediately) -------------------
    def toggle(self, field: str) -> bool:
        """Flip a boolean pref and return its new value."""
        if field not in BOOL_FIELDS:      # never setattr() a name we didn't define
            return bool(getattr(self, field, False))
        setattr(self, field, not getattr(self, field))
        self.save()
        return getattr(self, field)

    def set_intensity(self, value: str) -> bool:
        """Set the default intensity. Returns False if the name is unknown."""
        value = (value or "").lower()
        if value not in INTENSITIES:
            return False
        self.default_intensity = value
        self.save()
        return True

    def save(self) -> None:
        """Best-effort persist — a read-only disk must not break the bot."""
        try:
            settings.prefs_db.parent.mkdir(parents=True, exist_ok=True)
            settings.prefs_db.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:
            log.warning("could not save prefs to %s: %s", settings.prefs_db, exc)


def _load() -> Prefs:
    """
    Start from the .env defaults, then apply the stored overrides.

    Every stored value is type-checked before use: a hand-edited or truncated
    prefs.json must degrade to the .env default, never crash the bot on boot.
    """
    p = Prefs(
        confirm_before_edit=settings.confirm_before_edit,
        default_intensity=settings.default_intensity,
        skip_duplicates=settings.skip_duplicates,
    )

    try:
        raw = json.loads(settings.prefs_db.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return p
    except Exception as exc:
        log.warning("ignoring unreadable prefs file %s: %s", settings.prefs_db, exc)
        return p

    if not isinstance(raw, dict):
        return p
    for field in BOOL_FIELDS:
        if isinstance(raw.get(field), bool):
            setattr(p, field, raw[field])
    if raw.get("default_intensity") in INTENSITIES:
        p.default_intensity = raw["default_intensity"]

    log.info("prefs loaded from %s: %s", settings.prefs_db, asdict(p))
    return p


# The single instance every handler imports.
prefs = _load()
