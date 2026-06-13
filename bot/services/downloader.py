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

log = logging.getLogger(__name__)

# Quick sanity check so we only treat real http(s) URLs as links.
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def is_url(text: str) -> bool:
    return bool(text) and bool(URL_RE.search(text.strip()))


def extract_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def _ydl_opts(out_dir: Path, max_mb: int) -> dict:
    """
    Conservative options tuned for a Pi:
      * cap resolution at 1080p so we never download a giant 4K master,
      * single fragment-concurrency to avoid RAM/network spikes,
      * merge to mp4 for predictable downstream FFmpeg handling.
    """
    return {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "concurrent_fragment_downloads": 1,
        "retries": 3,
        "socket_timeout": 30,
        # Reject absurdly large files early (yt-dlp filesize filter, bytes).
        "max_filesize": max_mb * 1024 * 1024,
    }


def _blocking_download(url: str, out_dir: Path, max_mb: int) -> Path:
    with YoutubeDL(_ydl_opts(out_dir, max_mb)) as ydl:
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


async def download(url: str, out_dir: Path, max_mb: int) -> Path:
    """Download `url` into `out_dir`, returning the resulting file path."""
    log.info("downloading %s", url)
    return await asyncio.to_thread(_blocking_download, url, out_dir, max_mb)
