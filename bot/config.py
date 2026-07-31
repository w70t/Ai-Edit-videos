"""
Central configuration, loaded once from the environment / .env file.

Keeping everything in a single frozen dataclass means the rest of the code
imports `settings` and never touches os.environ directly — easy to test and
reason about.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env that sits next to the project root (one level up from this file).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# Kept as a literal rather than imported from services.editor: config sits at
# the bottom of the import graph and must not depend on a service.
# Must stay in sync with editor.BANDS.
INTENSITIES = ("light", "medium", "strong", "repost")


@dataclass(frozen=True)
class Settings:
    # --- Telegram ---
    bot_token: str = os.getenv("BOT_TOKEN", "").strip()
    admin_id: int = _int("ADMIN_ID", 0)
    forward_channel_id: str = os.getenv("FORWARD_CHANNEL_ID", "").strip()

    # --- Research ---
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "").strip()

    # --- Processing limits ---
    max_concurrent_jobs: int = _int("MAX_CONCURRENT_JOBS", 1)
    # Cap for link downloads (yt-dlp). Telegram uploads are additionally bound
    # by the Bot API limits below, which are much lower and not ours to raise.
    max_video_mb: int = _int("MAX_VIDEO_MB", 300)
    # Running your own Bot API server lifts both limits to 2000 MB.
    telegram_local_api: bool = _bool("TELEGRAM_LOCAL_API", False)
    default_intensity: str = os.getenv("DEFAULT_INTENSITY", "repost").strip().lower()
    # Ask which intensity to use before touching the video, instead of
    # rendering the default straight away.
    confirm_before_edit: bool = _bool("CONFIRM_BEFORE_EDIT", True)

    # --- Hardware ---
    hw_accel: str = os.getenv("HW_ACCEL", "auto").strip().lower()

    # --- Duplicate-input skipping ---
    # Skip re-rendering a source file the bot has already processed before.
    skip_duplicates: bool = _bool("SKIP_DUPLICATES", True)
    # Where the persistent hash registry is stored (survives restarts).
    dedup_db: Path = field(default_factory=lambda: Path(
        os.getenv("DEDUP_DB", "./dedup.json")).expanduser())

    # --- Storage ---
    work_dir: Path = field(default_factory=lambda: Path(
        os.getenv("WORK_DIR", "/tmp/ai-edit-videos")).expanduser())
    save_dir: Path = field(default_factory=lambda: Path(
        os.getenv("SAVE_DIR", "./saved")).expanduser())

    # --- Telegram Bot API hard limits (megabytes) --------------------------
    # getFile caps downloads at 20 MB and sendVideo caps uploads at 50 MB on the
    # public API. These are server-side; no setting on our end changes them.
    @property
    def max_incoming_mb(self) -> int:
        """Largest file the bot can download FROM Telegram."""
        return 2000 if self.telegram_local_api else 20

    @property
    def max_outgoing_mb(self) -> int:
        """Largest file the bot can send TO Telegram."""
        return 2000 if self.telegram_local_api else 50

    def validate(self) -> None:
        """Fail fast with a clear message if the bot can't possibly run."""
        problems = []
        if not self.bot_token:
            problems.append("BOT_TOKEN is missing")
        if not self.admin_id:
            problems.append("ADMIN_ID is missing or not a number")
        if self.default_intensity not in INTENSITIES:
            problems.append(
                f"DEFAULT_INTENSITY must be one of {'|'.join(INTENSITIES)}, got "
                f"'{self.default_intensity}'")

        if problems:
            raise SystemExit("Configuration error:\n  - " + "\n  - ".join(problems))

    def ensure_dirs(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.save_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
