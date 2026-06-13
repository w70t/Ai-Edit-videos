"""
The video editing engine.

Goal: apply *minimal, surgical* changes that alter the perceptual fingerprint
(frame hashes, audio fingerprint, container metadata) used by duplicate-content
detectors — while keeping the clip visually/aurally indistinguishable to a human.

Each intensity is a recipe of small nudges. We add a tiny per-render random
jitter so two renders at the same intensity are never byte-identical
(that's what "Generate New Variant" relies on).

Filter cheat-sheet (all values are deliberately subtle):
  crop/scale    -> shifts spatial frame hash without visible change
  eq            -> brightness/contrast/saturation micro-shift -> color hash
  hue           -> a fraction of a degree of hue rotation
  setpts/atempo -> ~0.5–2% speed change -> breaks frame-timing fingerprints
  asetrate      -> subtle pitch shift on the audio fingerprint
  hflip         -> horizontal mirror (strong only / opt-in)
  -map_metadata -1 -> strip all source metadata
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..utils.ffmpeg import hw_decode_args

log = logging.getLogger(__name__)


@dataclass
class EditOptions:
    """Per-render tweaks the admin can toggle from the 'settings' sub-menu."""
    flip: bool = False
    zoom: bool = False
    color: bool = True
    pitch: bool = False


@dataclass
class EditResult:
    output: Path
    intensity: str
    filters: str
    speed: float
    cmd: list[str] = field(default_factory=list)


def _rand(a: float, b: float) -> float:
    return round(random.uniform(a, b), 4)


def _build_filters(intensity: str, opts: EditOptions) -> tuple[str, str, float]:
    """
    Returns (video_filter_chain, audio_filter_chain, speed_factor).

    Numbers grow gently with intensity. Everything is randomized within a band
    so each variant is unique.
    """
    intensity = intensity.lower()

    # --- intensity bands: (crop_px, eq_strength, hue_deg, speed_jitter) ---
    bands = {
        "light":  (2,  0.01, 0.4, 0.005),
        "medium": (6,  0.03, 1.2, 0.012),
        "strong": (12, 0.06, 2.5, 0.020),
    }
    crop_px, eq_s, hue_deg, spd = bands.get(intensity, bands["medium"])

    vf: list[str] = []

    # 1) Subtle crop + rescale back to even dimensions (shifts spatial hash).
    c = random.randint(max(1, crop_px - 1), crop_px + 1)
    vf.append(f"crop=iw-{2*c}:ih-{2*c}:{c}:{c}")
    vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    # 2) Optional extra zoom (strong / opt-in) — a touch more crop punch.
    if opts.zoom:
        vf.append("scale=iw*1.04:ih*1.04,crop=iw/1.04:ih/1.04")

    # 3) Color micro-shift (default on; cheap and very effective on color hash).
    if opts.color:
        b = _rand(-eq_s, eq_s)
        ct = _rand(1 - eq_s, 1 + eq_s)
        sat = _rand(1 - eq_s, 1 + eq_s)
        vf.append(f"eq=brightness={b}:contrast={ct}:saturation={sat}")
        vf.append(f"hue=h={_rand(-hue_deg, hue_deg)}")

    # 4) Mirror flip (opt-in; strongest fingerprint change but visibly mirrored).
    if opts.flip:
        vf.append("hflip")

    # 5) Speed jitter via PTS (tiny — sub-2%).
    speed = round(1.0 + _rand(-spd, spd), 4)
    vf.append(f"setpts={round(1/speed, 6)}*PTS")

    # --- audio chain ---
    af: list[str] = [f"atempo={speed}"]
    if opts.pitch:
        # Shift sample rate up then resample back -> tiny pitch change.
        shift = _rand(1.0, 1.0 + eq_s)
        af.append(f"asetrate=44100*{shift},aresample=44100")

    return ",".join(vf), ",".join(af), speed


def _build_command(
    src: Path, dst: Path, vf: str, af: str, hw: bool
) -> list[str]:
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        # nice/low-priority friendly threading for the Pi
        "-threads", "3",
        *hw_decode_args(hw),
        "-i", str(src),
        "-vf", vf,
        "-af", af,
        # software H.264 encode — reliable on Pi 5, good for short social clips
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-map_metadata", "-1",          # strip ALL source metadata
        "-metadata", f"comment=v{random.randint(1000, 9999)}",  # fresh tag
        str(dst),
    ]


async def edit_video(
    src: Path,
    out_dir: Path,
    intensity: str = "medium",
    opts: EditOptions | None = None,
    hw: bool = True,
) -> EditResult:
    """
    Render an edited copy of `src` into `out_dir` and return an EditResult.

    Falls back to pure software decoding automatically if a hardware-accelerated
    attempt fails (some inputs aren't compatible with v4l2m2m).
    """
    opts = opts or EditOptions()
    vf, af, speed = _build_filters(intensity, opts)
    dst = out_dir / f"edited_{intensity}_{random.randint(1000, 9999)}.mp4"

    async def _run(use_hw: bool) -> int:
        cmd = _build_command(src, dst, vf, af, use_hw)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("ffmpeg (hw=%s) failed: %s", use_hw,
                        stderr.decode("utf-8", "ignore")[-400:])
        return proc.returncode

    rc = await _run(hw)
    if rc != 0 and hw:
        log.info("retrying render in software mode")
        rc = await _run(False)

    if rc != 0 or not dst.exists():
        raise RuntimeError("FFmpeg failed to render the edited video")

    return EditResult(
        output=dst,
        intensity=intensity,
        filters=vf,
        speed=speed,
        cmd=_build_command(src, dst, vf, af, hw),
    )
