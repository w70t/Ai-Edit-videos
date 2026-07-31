"""
Source acquisition via yt-dlp (Instagram Reels / TikTok / generic links).

yt-dlp is run in a worker thread because its API is blocking; this keeps the
aiogram event loop responsive while a download is in flight.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from yt_dlp import YoutubeDL

from ..config import settings
from .quality import Quality, format_selector
from .quality import get as get_tier

log = logging.getLogger(__name__)

# Quick sanity check so we only treat real http(s) URLs as links.
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def is_url(text: str) -> bool:
    return bool(text) and bool(URL_RE.search(text.strip()))


def extract_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def tier_cap_mb(tier: Quality) -> int:
    """
    The size ceiling actually applied: the tier's own cap, clamped by the
    global MAX_VIDEO_MB so a button press can never exceed what the disk
    was provisioned for.
    """
    return min(tier.max_mb, settings.max_video_mb)


def _ydl_opts(out_dir: Path, tier: Quality) -> dict:
    """
    Options tuned for a Pi:
      * resolution capped by the chosen tier (1080p unless the user has the
        high-quality grant and picked something bigger),
      * single fragment-concurrency to avoid RAM/network spikes,
      * merge to mp4 for predictable downstream FFmpeg handling.
    """
    return {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": format_selector(tier.max_height),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "concurrent_fragment_downloads": 1,
        "retries": 3,
        "socket_timeout": 30,
        # Reject absurdly large files early (yt-dlp filesize filter, bytes).
        # NOTE: this ABORTS the download, it does not fall back to a smaller
        # format — hence the tier-aware error message in `download()`.
        "max_filesize": tier_cap_mb(tier) * 1024 * 1024,
    }


def _blocking_download(url: str, out_dir: Path, tier: Quality) -> Path:
    with YoutubeDL(_ydl_opts(out_dir, tier)) as ydl:
        info = ydl.extract_info(url, download=True)
        # With merge_output_format the real file is the mp4 in out_dir.
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
        if not path.exists():
            # Fall back to whatever single file landed in the dir.
            candidates = sorted(out_dir.glob("source.*"))
            if not candidates:
                raise FileNotFoundError("yt-dlp produced no output file")
            path = candidates[0]
        return path


async def download(url: str, out_dir: Path, quality: str) -> Path:
    """
    Download `url` into `out_dir` at the given quality tier.

    A size abort is translated into a message that names the tier, because
    "File is larger than max-filesize" tells the user nothing about the one
    button that would fix it.
    """
    tier = get_tier(quality)
    log.info("downloading %s at %s (cap %d MB)", url, tier.key, tier_cap_mb(tier))
    try:
        return await asyncio.to_thread(_blocking_download, url, out_dir, tier)
    except Exception as exc:
        if "max-filesize" in str(exc).lower() or "larger than" in str(exc).lower():
            raise RuntimeError(
                f"الفيديو أكبر من سقف جودة «{tier.label}» "
                f"({tier_cap_mb(tier)} ميجابايت). اختر جودة أقل من زر 🎚 الجودة."
            ) from exc
        raise
