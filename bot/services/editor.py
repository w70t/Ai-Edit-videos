"""
The video editing engine.

WHAT ACTUALLY MATTERS
---------------------
Platforms do not compare files byte-for-byte. They compare *perceptual*
fingerprints — a visual hash (Meta open-sourced theirs: PDQ for frames,
TMK+PDQF for video) plus an audio fingerprint. Those are engineered to survive
exactly the things a naive "uniquifier" does:

    re-encoding, metadata stripping, a few pixels of crop,
    1-3% brightness/contrast, a degree of hue, sub-2% speed change

So those operations change the SHA-256 and nothing else. They are cosmetic.
What genuinely moves a perceptual fingerprint is:

    1. temporal edits   — removing time from the ends breaks TMK alignment
    2. reframing        — a real zoom (8-12%) with an off-centre crop window
    3. audio pitch/tempo — ~4-6% shift breaks the audio fingerprint
    4. overlays / aspect-ratio changes (visible; not applied here)

The recipes below are honest about which bucket each operation falls into, and
`EditPlan.notes` carries that assessment all the way to the user so the bot
never claims more than it did.

Filter cheat-sheet:
  crop/scale    -> reframe: crop a smaller window off-centre, scale back to size
  eq / hue      -> colour micro-shift (cosmetic)
  setpts/atempo -> playback speed jitter
  asetrate      -> real pitch shift, resampled back to the source rate
  hflip         -> horizontal mirror (opt-in; Meta's PDQ checks mirrored hashes)
  -ss / -t      -> temporal trim, applied on the INPUT so filters see the cut clip
  -map_metadata -1 -> strip all source metadata
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..utils.ffmpeg import hw_decode_args, probe

log = logging.getLogger(__name__)

# Never trim a clip below this many seconds of remaining runtime.
MIN_KEEP_SECONDS = 3.0
# Never remove more than this fraction of the clip.
MAX_TRIM_FRACTION = 0.15
# The central fraction of the frame a reframe must never crop into. Creators put
# text, faces and captions there; eating it is what makes a repost look wrong.
SAFE_AREA = 0.85

# Impact markers used in the user-facing notes.
HIGH, MED, LOW = "🔴", "🟡", "⚪"


@dataclass
class EditOptions:
    """Per-render tweaks the admin can toggle from the 'settings' sub-menu."""
    flip: bool = False
    zoom: bool = False
    color: bool = True
    pitch: bool = False
    trim: bool = False
    # --- Reach guards -------------------------------------------------------
    # These two deliberately WEAKEN fingerprint evasion to protect the signals
    # that actually drive distribution. Both default to protecting reach.
    protect_hook: bool = True      # take the trim off the tail, never the opening
    trending_audio: bool = False   # keep the audio matchable to a trending sound


# The toggles a callback button is allowed to flip (guards setattr).
TOGGLEABLE = ("flip", "zoom", "color", "pitch", "trim",
              "protect_hook", "trending_audio")


@dataclass(frozen=True)
class _Band:
    """Magnitudes for one intensity preset."""
    crop_px: int        # cosmetic border crop, pixels per side (when no reframe)
    eq: float           # brightness / contrast / saturation magnitude
    hue: float          # hue rotation, degrees
    speed: float        # playback speed jitter
    zoom: float         # reframe zoom factor (0 = no reframe)
    reframe: float      # how far off-centre the crop window may sit (0..1)
    trim: float         # seconds budget to remove across both ends
    pitch: float        # audio pitch/tempo shift magnitude


BANDS: dict[str, _Band] = {
    # Cosmetic only — deliberately kept as the "change almost nothing" option.
    "light":  _Band(crop_px=2,  eq=0.01, hue=0.4, speed=0.005,
                    zoom=0.00, reframe=0.00, trim=0.0, pitch=0.010),
    # A real but subtle reframe; still no temporal edit.
    "medium": _Band(crop_px=6,  eq=0.03, hue=1.2, speed=0.012,
                    zoom=0.04, reframe=0.15, trim=0.0, pitch=0.020),
    "strong": _Band(crop_px=12, eq=0.06, hue=2.5, speed=0.020,
                    zoom=0.08, reframe=0.35, trim=0.8, pitch=0.040),
    # Everything that actually moves a perceptual fingerprint, at the largest
    # magnitude that still looks like the original to a viewer.
    "repost": _Band(crop_px=8,  eq=0.04, hue=1.5, speed=0.015,
                    zoom=0.11, reframe=0.55, trim=1.5, pitch=0.055),
}

INTENSITIES = tuple(BANDS)


def preset_options(intensity: str) -> EditOptions:
    """
    The option set a preset turns on when the admin picks it.

    Every preset ships with the reach guards on: the hook is protected by
    default, and the trending-audio switch is left to the admin because only
    they know whether the clip rides a trending sound.
    """
    intensity = (intensity or "").lower()
    if intensity in ("repost", "strong"):
        return EditOptions(flip=False, zoom=True, color=True, pitch=True,
                           trim=True, protect_hook=True, trending_audio=False)
    if intensity == "medium":
        return EditOptions(flip=False, zoom=True, color=True, pitch=False,
                           trim=False, protect_hook=True, trending_audio=False)
    return EditOptions()  # light: colour only


@dataclass
class EditPlan:
    """Everything the ffmpeg command needs, decided up front and kept stable."""
    vf: str
    af: str
    speed: float
    trim_start: float
    trim_end: float
    out_duration: float          # 0 = no explicit -t
    tag: str                     # fresh metadata comment (computed once)
    created_iso: str             # fresh creation_time (computed once)
    notes: list[str] = field(default_factory=list)

    @property
    def trimmed(self) -> float:
        """Total seconds removed from the clip."""
        return round(self.trim_start + self.trim_end, 2)


@dataclass
class EditResult:
    output: Path
    intensity: str
    plan: EditPlan
    cmd: list[str] = field(default_factory=list)


def _rand(a: float, b: float) -> float:
    return round(random.uniform(a, b), 4)


def _even(value: float, minimum: int = 2) -> int:
    """Round down to an even integer — libx264/yuv420p rejects odd dimensions."""
    return max(minimum, int(value) // 2 * 2)


def _plan_trim(duration: float, budget: float,
               protect_hook: bool) -> tuple[float, float]:
    """
    Decide how many seconds to cut off the head and the tail.

    Trimming is the single most effective edit against video fingerprinting and
    it is invisible to a viewer — but it must never eat a short clip alive,
    hence the fraction cap and the minimum-runtime floor.

    `protect_hook` moves the whole cut to the tail. That is measurably weaker
    against temporal alignment (the opening still matches frame-for-frame), but
    watch-through in the first seconds is the strongest ranking signal there is,
    so trading evasion for the hook is usually the right call.
    """
    if budget <= 0 or duration <= 0:
        return 0.0, 0.0
    total = min(budget, duration * MAX_TRIM_FRACTION)
    if duration - total < MIN_KEEP_SECONDS:
        total = duration - MIN_KEEP_SECONDS
    if total < 0.2:
        return 0.0, 0.0
    if protect_hook:
        return 0.0, round(total, 2)
    head = round(random.uniform(0.25, 0.75) * total, 2)
    return head, round(total - head, 2)


def _safe_offset(outer: int, window: int, reframe: float) -> int:
    """
    Pick a crop offset that keeps the central SAFE_AREA of the frame intact.

    The window may only sit where it still fully covers the safe band, so a
    reframe can never slice text or a face off the edge. The allowed interval is
    symmetric around the centred offset; `reframe` says how much of it to use.
    """
    slack = outer - window
    if slack <= 0:
        return 0
    lo = max(0.0, outer * (1 + SAFE_AREA) / 2 - window)
    hi = min(float(slack), outer * (1 - SAFE_AREA) / 2)
    if hi < lo:
        # Zoom is too deep to honour the safe area — stay dead centre.
        return _even(slack / 2, minimum=0)
    span = (hi - lo) / 2 * reframe
    offset = slack / 2 + random.uniform(-span, span)
    return _even(min(max(offset, lo), hi), minimum=0)


def _plan_reframe(width: int, height: int, band: _Band) -> str | None:
    """
    Crop a smaller, off-centre window and scale it back to the original size.

    Working in concrete integers (rather than ffmpeg expressions) lets us
    guarantee even dimensions AND an even crop offset — odd values either fail
    the encode outright or shift the chroma plane.
    """
    if band.zoom <= 0:
        return None

    # Cap the zoom so the safe area can always fit inside the crop window.
    factor = 1.0 + random.uniform(band.zoom * 0.8, band.zoom * 1.2)
    factor = min(factor, 1 / SAFE_AREA)

    if width >= 2 and height >= 2:
        out_w, out_h = _even(width), _even(height)
        crop_w, crop_h = _even(out_w / factor), _even(out_h / factor)
        x = _safe_offset(out_w, crop_w, band.reframe)
        y = _safe_offset(out_h, crop_h, band.reframe)
        return f"crop={crop_w}:{crop_h}:{x}:{y},scale={out_w}:{out_h}"

    # Dimensions unknown (probe failed): centred fallback, still always even.
    f = round(factor, 4)
    return (f"scale=trunc(iw*{f}/2)*2:trunc(ih*{f}/2)*2,"
            f"crop=trunc(iw/{f}/2)*2:trunc(ih/{f}/2)*2")


def _build_plan(intensity: str, opts: EditOptions, info: dict) -> EditPlan:
    """Turn an intensity + options + source facts into a concrete render plan."""
    band = BANDS.get((intensity or "").lower(), BANDS["medium"])

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    duration = float(info.get("duration") or 0.0)
    # Fall back only when ffprobe genuinely gave us nothing.
    sample_rate = int(info.get("sample_rate") or 0) or 44100

    vf: list[str] = []
    notes: list[str] = []

    # --- 1) Spatial ---------------------------------------------------------
    reframe = _plan_reframe(width, height, band) if opts.zoom else None
    if reframe:
        vf.append(reframe)
        notes.append(
            f"{HIGH} تقريب وإعادة تأطير — يزيح البصمة المرئية فعلياً "
            f"(وسط الإطار {int(SAFE_AREA * 100)}% محمي من القص)")
    else:
        c = random.randint(max(1, band.crop_px - 1), band.crop_px + 1)
        vf.append(f"crop=iw-{2 * c}:ih-{2 * c}:{c}:{c}")
        # Scale back to the exact source size. Without this the clip comes out
        # a few pixels smaller with a slightly-off aspect ratio, which the
        # platforms then re-crop on upload.
        if width >= 2 and height >= 2:
            vf.append(f"scale={_even(width)}:{_even(height)}")
        else:
            vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        notes.append(f"{LOW} قص حوافّ طفيف — تجميلي، لا يكسر البصمة المرئية")

    # --- 2) Colour (cosmetic, but free) -------------------------------------
    if opts.color:
        vf.append(
            f"eq=brightness={_rand(-band.eq, band.eq)}"
            f":contrast={_rand(1 - band.eq, 1 + band.eq)}"
            f":saturation={_rand(1 - band.eq, 1 + band.eq)}")
        vf.append(f"hue=h={_rand(-band.hue, band.hue)}")
        notes.append(f"{LOW} إزاحة ألوان — تجميلية، البصمة تصمد أمامها")

    # --- 3) Mirror (opt-in) -------------------------------------------------
    if opts.flip:
        vf.append("hflip")
        notes.append(f"{MED} عكس مرآة — ميتا تفحص البصمات المعكوسة، أثره متوسط")

    # --- 4) Speed jitter ----------------------------------------------------
    speed = round(1.0 + _rand(-band.speed, band.speed), 4)
    vf.append(f"setpts={round(1 / speed, 6)}*PTS")

    # --- 5) Temporal trim (the strongest lever) -----------------------------
    trim_start, trim_end = (
        _plan_trim(duration, band.trim, opts.protect_hook) if opts.trim
        else (0.0, 0.0))
    out_duration = 0.0
    if trim_start or trim_end:
        out_duration = max(0.0, duration - trim_start - trim_end)
        if opts.protect_hook:
            notes.append(
                f"{HIGH} قص زمني {trim_end:.1f} ثانية من النهاية — "
                f"الـ hook محمي كاملاً (كسر أضعف قليلاً، لكن أول الفيديو سليم)")
        else:
            notes.append(
                f"{HIGH} قص زمني {trim_start + trim_end:.1f} ثانية "
                f"({trim_start:.1f} بداية / {trim_end:.1f} نهاية) — أقوى كسر زمني، "
                f"لكنه يمسّ الـ hook")
    elif opts.trim:
        # Asked for, but the clip is too short to give any away — say so rather
        # than let the user assume it happened.
        notes.append(
            f"{LOW} تُخطّي القص الزمني — المقطع قصير جداً "
            f"({duration:.1f} ثانية) ولا يحتمل الاقتطاع")

    # --- 6) Audio -----------------------------------------------------------
    # asetrate shifts pitch AND tempo by `shift`; the follow-up atempo pulls the
    # net tempo back to `speed` so the audio stays locked to the video.
    af: list[str] = []
    if opts.trending_audio:
        # Shifting the pitch would stop the platform recognising the track, which
        # costs the sound's discovery page — usually worth more than the extra
        # evasion. Leave the audio matchable and say so.
        af.append(f"atempo={speed}")
        notes.append(
            f"{LOW} الصوت بلا إزاحة — وضع «صوت رائج»: تبقى مرتبطاً بالمقطع "
            f"الصوتي وتتنازل عن كسر بصمته")
    elif opts.pitch and band.pitch > 0:
        magnitude = random.uniform(band.pitch * 0.8, band.pitch * 1.2)
        shift = 1 + magnitude if random.random() < 0.5 else 1 - magnitude
        af.append(f"asetrate={int(sample_rate * shift)}")
        af.append(f"aresample={sample_rate}")
        af.append(f"atempo={round(speed / shift, 6)}")
        notes.append(
            f"{HIGH} إزاحة طبقة الصوت {magnitude * 100:.1f}% — يكسر البصمة الصوتية")
    else:
        af.append(f"atempo={speed}")
        notes.append(f"{LOW} الصوت شبه كما هو — البصمة الصوتية تبقى قابلة للمطابقة")

    return EditPlan(
        vf=",".join(vf),
        af=",".join(af),
        speed=speed,
        trim_start=trim_start,
        trim_end=trim_end,
        out_duration=out_duration,
        tag=f"v{random.randint(1000, 9999)}",
        created_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        notes=notes,
    )


def impact_label(notes: list[str]) -> str:
    """A one-word, honest verdict on how much this render moved the needle."""
    strong = sum(1 for n in notes if n.startswith(HIGH))
    if strong >= 2:
        return "عالٍ"
    if strong == 1 or any(n.startswith(MED) for n in notes):
        return "متوسط"
    return "منخفض جداً"


def _build_command(
    src: Path, dst: Path, plan: EditPlan, hw: bool, has_audio: bool
) -> list[str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        # nice/low-priority friendly threading for the Pi
        "-threads", "3",
        *hw_decode_args(hw),
    ]
    # Trim on the INPUT side: cheaper than a filter, and the -vf chain then
    # operates on the already-shortened clip.
    if plan.trim_start > 0:
        cmd += ["-ss", f"{plan.trim_start:.3f}"]
    if plan.out_duration > 0:
        cmd += ["-t", f"{plan.out_duration:.3f}"]

    cmd += ["-i", str(src), "-vf", plan.vf]

    # Apply audio filters / re-encode only when the source actually has audio
    # (video notes, GIFs and some clips don't) — otherwise drop audio with -an.
    # Force standard stereo 44.1kHz AAC-LC: iOS Photos rejects odd sample rates.
    if has_audio:
        cmd += ["-af", plan.af, "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-an"]

    cmd += [
        # software H.264 encode — reliable on Pi 5, good for short social clips.
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        # Explicit iOS-friendly profile/level (broad device compatibility).
        "-profile:v", "high",
        "-level", "4.0",
        "-pix_fmt", "yuv420p",
        # Force a CONSTANT frame rate. Social clips are often VFR, and iOS Photos
        # refuses to import VFR video even when Telegram shows the save option.
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-map_metadata", "-1",                        # strip ALL source metadata
        "-metadata", f"comment={plan.tag}",           # fresh tag (stable per plan)
        "-metadata", f"creation_time={plan.created_iso}",  # iOS Photos import
        str(dst),
    ]
    return cmd


async def edit_video(
    src: Path,
    out_dir: Path,
    intensity: str = "medium",
    opts: EditOptions | None = None,
    hw: bool = True,
    info: dict | None = None,
) -> EditResult:
    """
    Render an edited copy of `src` into `out_dir` and return an EditResult.

    `info` is an ffprobe dict for the source; pass the cached one to avoid
    probing twice. Falls back to pure software decoding automatically if a
    hardware-accelerated attempt fails.
    """
    opts = opts or EditOptions()
    if info is None:
        info = await probe(str(src))

    plan = _build_plan(intensity, opts, info)
    has_audio = bool(info.get("has_audio", True))
    dst = out_dir / f"edited_{intensity}_{random.randint(1000, 9999)}.mp4"

    executed: list[str] = []

    async def _run(use_hw: bool) -> int:
        nonlocal executed
        # Rebuilt per attempt only to swap the hwaccel flags — the plan (and so
        # the filters, trim and metadata) is identical, so `executed` is always
        # the command that actually produced the file.
        executed = _build_command(src, dst, plan, use_hw, has_audio)
        proc = await asyncio.create_subprocess_exec(
            *executed,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("ffmpeg (hw=%s) failed: %s", use_hw,
                        stderr.decode("utf-8", "ignore")[-400:])
        return proc.returncode

    # Only worth retrying if the first attempt actually used a hardware path;
    # otherwise the "fallback" would rerun the identical command.
    used_hw = bool(hw_decode_args(hw))
    rc = await _run(hw)
    if rc != 0 and used_hw:
        log.info("retrying render in software mode")
        rc = await _run(False)

    if rc != 0 or not dst.exists():
        raise RuntimeError("FFmpeg failed to render the edited video")

    return EditResult(output=dst, intensity=intensity, plan=plan, cmd=executed)
