"""
Quality tiers — one place that decides both what we *download* and how we
*encode*.

Everything about quality is picked from a button, never from the environment,
so the admin can change their mind per-video without touching a config file.

Two halves per tier
-------------------
1. Acquisition: the height cap handed to yt-dlp's format selector, plus a size
   ceiling so a Pi never fills its disk with a 4K master.
2. Encoding: CRF / x264 preset / audio bitrate. A higher tier is not just a
   bigger frame — re-encoding a 4K source at CRF 23 veryfast throws away most
   of what the extra pixels bought.

The H.264 level matters more than it looks: level 4.0 tops out at 8192
macroblocks per frame. Stamping `-level 4.0` on a 4K stream produces a file
that claims a level its own resolution violates, which some players and iOS
reject outright. `level_for()` keeps the tag honest.

Note it measures frame *area*, not height — the constraint is macroblocks per
frame, and everything this bot touches is vertical. A 1080×1920 Reel has the
exact same area as a 1920×1080 clip and belongs at the same level 4.0; judging
by height alone would push every ordinary vertical 1080p video up to 5.x and
throw away the broad phone compatibility 4.0 buys.

Only STANDARD is ungated. Everything above it needs permission — see
services.users — because the render cost lands on one shared Raspberry Pi.
"""

from __future__ import annotations

from dataclasses import dataclass

# The tier every new user gets, and the one the gate falls back to.
STANDARD = "1080"


@dataclass(frozen=True)
class Quality:
    key: str
    label: str          # what the button says
    max_height: int     # 0 = no cap: take the best format that exists
    max_mb: int         # abort a download larger than this
    crf: int            # x264 rate factor — lower is better and bigger
    preset: str         # x264 speed/efficiency trade-off
    audio_bitrate: str
    note: str           # one line shown in the picker, honest about the cost

    @property
    def gated(self) -> bool:
        """True when this tier needs an explicit grant from the admin."""
        return self.key != STANDARD


# Preset choice, and why it is not simply "slower is better":
#   * 1440p gets `slow` — it is the sweet spot where the extra analysis still
#     finishes in a sane time on a Pi 5.
#   * 4K gets `medium` on purpose. At CRF 18 a 4K frame already carries far
#     more bits than the encoder needs to hide artefacts, so `slow` buys a few
#     percent of bitrate for roughly triple the wall-clock. On a Pi that turns
#     a long wait into an unusable one.
TIERS: dict[str, Quality] = {
    STANDARD: Quality(
        key=STANDARD, label="1080p · عادي",
        max_height=1080, max_mb=300,
        crf=23, preset="veryfast", audio_bitrate="128k",
        note="الأسرع — الوضع الافتراضي للجميع",
    ),
    "1440": Quality(
        key="1440", label="1440p · 2K",
        max_height=1440, max_mb=700,
        crf=18, preset="slow", audio_bitrate="192k",
        note="أوضح بوضوح · الرندر أبطأ ~4 أضعاف",
    ),
    "2160": Quality(
        key="2160", label="2160p · 4K",
        max_height=2160, max_mb=1500,
        crf=18, preset="medium", audio_bitrate="192k",
        note="أعلى دقة عملية · الرندر بطيء جداً على الـ Pi",
    ),
    "best": Quality(
        key="best", label="أعلى جودة متاحة",
        max_height=0, max_mb=2000,
        crf=18, preset="medium", audio_bitrate="192k",
        note="بدون سقف — ينزّل أضخم صيغة موجودة مهما كانت",
    ),
}

# Display order for the picker (dict order is insertion order, but be explicit).
ORDER = (STANDARD, "1440", "2160", "best")


def get(key: str) -> Quality:
    """Look a tier up, falling back to STANDARD for anything unknown."""
    return TIERS.get((key or "").strip().lower(), TIERS[STANDARD])


def resolve(key: str, allowed_hq: bool) -> Quality:
    """
    The tier a user actually gets.

    A gated tier requested by someone without the grant silently degrades to
    STANDARD rather than erroring — the caller decides whether to say so.
    """
    tier = get(key)
    if tier.gated and not allowed_hq:
        return TIERS[STANDARD]
    return tier


# H.264 MaxFS — the largest frame, in macroblocks, each level permits.
# 1920x1080 and 1080x1920 both come to 8160 MBs, so both sit inside 4.0.
_MAX_MACROBLOCKS = ((8192, "4.0"), (22080, "5.0"))
_ABOVE_ALL = "5.2"      # 4K and up; also covers 60fps, which 5.1 does not


def level_for(width: int, height: int) -> str:
    """
    The lowest `-level` tag this frame size definitely satisfies.

    4.0 is preferred wherever it fits because it is the most broadly compatible
    value for phone playback. Overshooting is safe (a decoder just needs to
    support the level); undershooting is what breaks players, so unknown
    dimensions fall back to 4.0 — the encoder is then free to correct it.
    """
    if width <= 0 or height <= 0:
        return "4.0"
    # Macroblocks are 16x16, partial ones still count.
    blocks = -(-width // 16) * -(-height // 16)
    for limit, level in _MAX_MACROBLOCKS:
        if blocks <= limit:
            return level
    return _ABOVE_ALL


def format_selector(max_height: int) -> str:
    """
    yt-dlp format string for a height cap.

    The trailing bare `/b` is deliberate: if a source exists *only* above the
    cap, taking it beats failing the job outright.
    """
    if max_height <= 0:
        return "bv*+ba/b"
    return (f"bv*[height<={max_height}]+ba/"
            f"b[height<={max_height}]/"
            f"bv*+ba/b")
