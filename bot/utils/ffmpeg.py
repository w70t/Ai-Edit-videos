"""
FFmpeg / FFprobe helpers: capability detection and metadata probing.

On a Raspberry Pi 5 the most reliable hardware path is the V4L2 M2M H.264
*decoder* (`h264_v4l2m2m`). Encoding on the Pi 5 is realistically software
(libx264) for short social clips — that is fast enough and far more compatible,
so we only hardware-accelerate the *decode* side and keep a clean software
fallback.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from functools import lru_cache


@lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@lru_cache(maxsize=1)
def _decoders_blob() -> str:
    """Run `ffmpeg -decoders` once and cache the raw text."""
    try:
        import subprocess
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-decoders"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout + out.stderr
    except Exception:
        return ""


@lru_cache(maxsize=1)
def has_v4l2_decoder() -> bool:
    """True if the Pi's hardware H.264 decoder is exposed to FFmpeg."""
    return "h264_v4l2m2m" in _decoders_blob()


def hw_decode_args(enabled: bool) -> list[str]:
    """
    Input-side flags that go *before* `-i`.
    Returns an empty list when hardware decode is off/unavailable.
    """
    if enabled and has_v4l2_decoder():
        # Let FFmpeg pick the v4l2 path; safe no-op on non-H.264 inputs because
        # we still keep software fallback at the call site.
        return ["-hwaccel", "v4l2m2m"]
    return []


async def make_thumbnail(src: str, dst: str) -> bool:
    """
    Grab a single frame as a JPEG thumbnail for Telegram.

    Telegram requires the thumb to be a JPEG, ≤320px on the long side and under
    ~200KB. We grab a frame ~1s in and downscale while *preserving aspect ratio*
    (no padding -> no black box). Returns True on success.
    """
    from pathlib import Path

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "1", "-i", src,
        "-frames:v", "1",
        "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
        "-q:v", "5",
        dst,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode == 0 and Path(dst).exists()


async def probe(path: str) -> dict:
    """
    Return basic stream info via ffprobe: duration, width, height, codec, size.
    Never raises — returns {} on any failure so callers can degrade gracefully.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return {}

    try:
        data = json.loads(stdout.decode("utf-8", "ignore"))
    except json.JSONDecodeError:
        return {}

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "duration": float(fmt.get("duration", 0) or 0),
        "size": int(fmt.get("size", 0) or 0),
        "width": int(video.get("width", 0) or 0),
        "height": int(video.get("height", 0) or 0),
        "codec": video.get("codec_name", "unknown"),
        "has_audio": has_audio,
    }
