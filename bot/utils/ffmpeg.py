"""
FFmpeg / FFprobe helpers: capability detection and metadata probing.

Hardware decode, honestly
-------------------------
`v4l2m2m` is NOT a valid `-hwaccel` value — it is a *codec* family
(`h264_v4l2m2m`), and passing it makes ffmpeg abort with "Unrecognized
hwaccel" before it even opens the input. Checking `ffmpeg -decoders` for
`h264_v4l2m2m` does not help either: Raspberry Pi OS ships that decoder
compiled in whether or not the SoC has the block, and the Pi 5 dropped the
H.264 hardware decoder entirely (only HEVC decode remains).

So capability is resolved once at startup, in two steps:
  1. the method must appear in `ffmpeg -hwaccels` — that list is the only
     authority on what `-hwaccel` accepts,
  2. it must survive a real test decode, because being listed proves the build
     has the code, not that this machine's hardware answers.

Resolving once means a machine with no usable hardware path never pays for a
failed render + software retry on every single job. Encoding stays software
(libx264): fast enough for short social clips on a Pi 5 and far more compatible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# Tried in order when HW_ACCEL=auto. Decode-side only.
HWACCEL_PREFERENCE = ("vaapi", "drm", "vdpau", "qsv", "cuda")

# Resolved once by resolve_hwaccel(); "" means pure software.
_resolved: str = ""


@lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@lru_cache(maxsize=1)
def _decoders_blob() -> str:
    """Run `ffmpeg -decoders` once and cache the raw text."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-decoders"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout + out.stderr
    except Exception:
        return ""


@lru_cache(maxsize=1)
def has_v4l2_decoder() -> bool:
    """
    True if `h264_v4l2m2m` is compiled into this ffmpeg.

    Informational only — it says nothing about whether the hardware exists, and
    it is NOT a valid `-hwaccel` value. Kept for the startup log line.
    """
    return "h264_v4l2m2m" in _decoders_blob()


@lru_cache(maxsize=1)
def available_hwaccels() -> frozenset[str]:
    """The methods `ffmpeg -hwaccels` reports — the only valid -hwaccel values."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True, text=True, timeout=15,
        )
        lines = (out.stdout + out.stderr).splitlines()
        # First line is the "Hardware acceleration methods:" header.
        return frozenset(
            ln.strip() for ln in lines
            if ln.strip() and not ln.rstrip().endswith(":")
        )
    except Exception:
        return frozenset()


async def _run_quiet(cmd: list[str], timeout: int = 20) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0
    except Exception:
        return False


async def _make_probe_clip(path: Path) -> bool:
    """
    Encode a tiny real H.264 file to test hardware DECODE against.

    A lavfi source would be useless here: it synthesizes raw frames, so nothing
    is decoded and every method would appear to "work".
    """
    return await _run_quiet([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=0.5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    ])


async def _hwaccel_works(method: str, sample: Path) -> bool:
    """Decode the sample H.264 clip with `method`; True if ffmpeg manages it."""
    return await _run_quiet([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", method, "-i", str(sample), "-f", "null", "-",
    ])


async def resolve_hwaccel(setting: str) -> str:
    """
    Turn the HW_ACCEL setting into a usable `-hwaccel` value, or "" for software.

    Call once at startup. `auto` walks HWACCEL_PREFERENCE; an explicit method is
    validated and warned about if unusable, never silently swapped.
    """
    global _resolved
    setting = (setting or "auto").strip().lower()

    if setting in ("off", "none", "software", "cpu"):
        _resolved = ""
        log.info("hw decode: disabled by configuration (software only)")
        return ""

    listed = available_hwaccels()
    candidates = [m for m in (HWACCEL_PREFERENCE if setting == "auto"
                              else (setting,))]
    for method in candidates:
        if method not in listed and setting != "auto":
            log.warning(
                "hw decode: '%s' is not a valid -hwaccel on this ffmpeg "
                "(available: %s) — falling back to software",
                method, ", ".join(sorted(listed)) or "none")
    candidates = [m for m in candidates if m in listed]

    if candidates:
        with tempfile.TemporaryDirectory(prefix="hwprobe-") as tmp:
            sample = Path(tmp) / "probe.mp4"
            if not await _make_probe_clip(sample):
                log.warning("hw decode: could not build a probe clip — "
                            "assuming software")
                candidates = []
            for method in candidates:
                if await _hwaccel_works(method, sample):
                    _resolved = method
                    log.info("hw decode: using '%s'", method)
                    return method
                log.info("hw decode: '%s' is listed but failed a test decode",
                         method)

    _resolved = ""
    log.info("hw decode: no usable method — software decode "
             "(expected on a Pi 5: it has no H.264 hardware decoder)")
    return ""


def hw_decode_args(enabled: bool) -> list[str]:
    """
    Input-side flags that go *before* `-i`.

    Empty when hardware decode is off or nothing usable was resolved — which is
    also what makes the caller's software retry a genuine second attempt rather
    than a rerun of the same failing command.
    """
    return ["-hwaccel", _resolved] if enabled and _resolved else []


async def make_thumbnail(src: str, dst: str) -> bool:
    """
    Grab a single frame as a JPEG thumbnail for Telegram.

    Telegram requires the thumb to be a JPEG, ≤320px on the long side and under
    ~200KB. We grab a frame ~1s in and downscale while *preserving aspect ratio*
    (no padding -> no black box). Returns True on success.
    """
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
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(fmt.get("duration", 0) or 0),
        "size": int(fmt.get("size", 0) or 0),
        "width": int(video.get("width", 0) or 0),
        "height": int(video.get("height", 0) or 0),
        "codec": video.get("codec_name", "unknown"),
        "has_audio": bool(audio),
        # The editor needs the REAL rate: pitch shifting assumes 44.1kHz breaks
        # badly on 48kHz sources (which is most TikTok / Reels material).
        "sample_rate": int(audio.get("sample_rate", 0) or 0),
    }
