"""
Glue between the queue, the editor, and Telegram.

`render_and_send` is what a queued job actually runs: it (re)renders the video
at the requested intensity/options and delivers it to the admin with the control
keyboard attached.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from ..config import settings
from ..keyboards import result_keyboard
from ..utils.cleanup import remove_path
from ..utils.ffmpeg import make_thumbnail, probe
from . import downloader, quality
from .dedup import registry
from .editor import edit_video, impact_label
from .storage import JobRecord
from .users import registry as users

log = logging.getLogger(__name__)

INTENSITY_LABEL = {
    "light": "🟢 خفيف",
    "medium": "🟡 متوسط",
    "strong": "🔴 قوي",
    "repost": "♻️ إعادة نشر",
}


def _caption(rec: JobRecord) -> str:
    flags = []
    o = rec.options
    if o.flip:
        flags.append("عكس")
    if o.zoom:
        flags.append("تقريب")
    if o.color:
        flags.append("ألوان")
    if o.pitch:
        flags.append("طبقة الصوت")
    if o.trim:
        flags.append("قص زمني")
    extras = (" · " + "، ".join(flags)) if flags else ""
    label = INTENSITY_LABEL.get(rec.intensity, rec.intensity)
    variant = f" · نسخة #{rec.variant_count}" if rec.variant_count else ""
    tier = quality.get(rec.quality)
    # Say plainly how much this render is expected to matter — a green tick
    # alone would imply more than a cosmetic pass actually achieves.
    verdict = f"\n🎯 أثر متوقع على كشف التكرار: {impact_label(rec.edit_notes)}"
    return (f"✅ تم — تعديل *{label}*{extras}{variant}\n"
            f"🎚 الجودة: *{tier.label}*{verdict}")


async def refetch_source(rec: JobRecord) -> None:
    """
    Re-download a link job's source at `rec.quality`.

    Only meaningful for links: a Telegram upload is already the compressed copy
    Telegram handed us, and no tier can conjure back pixels it never sent.
    """
    if not rec.from_link:
        return
    if rec.source and rec.source.exists():
        remove_path(rec.source)
    rec.source = await downloader.download(rec.origin, rec.work_dir, rec.quality)
    # The file changed, so every cached fact about it is stale.
    rec.probe = {}
    rec.source_hash = ""


async def render_and_send(
    bot: Bot,
    rec: JobRecord,
    status_chat_id: int,
    status_message_id: int | None = None,
) -> None:
    """
    Render `rec.source` at the record's current intensity/options and send the
    result. Updates `rec.output`, timings and probe info in place.
    """
    assert rec.source is not None, "job has no source file"

    # Probe once (used by the "Show Processing Info" button).
    if not rec.probe:
        rec.probe = await probe(str(rec.source))

    if status_message_id:
        try:
            await bot.edit_message_text(
                "⚙️ جارٍ تنفيذ التعديل… (FFmpeg)",
                chat_id=status_chat_id,
                message_id=status_message_id,
            )
        except Exception:
            pass

    # Drop the previous render before making a new one (keep temp small).
    if rec.output and rec.output.exists():
        remove_path(rec.output)

    started = time.monotonic()
    result = await edit_video(
        src=rec.source,
        out_dir=rec.work_dir,
        intensity=rec.intensity,
        opts=rec.options,
        hw=(settings.hw_accel != "off"),
        info=rec.probe,          # reuse the probe above instead of running it twice
        quality=rec.quality,     # decides CRF / preset / audio bitrate
    )
    rec.output = result.output
    rec.edit_notes = result.plan.notes
    rec.last_render_seconds = round(time.monotonic() - started, 2)

    # A link download may be far larger than anything we can send back: the Bot
    # API caps uploads at 50 MB. Only the finished render tells us the real
    # size, so check here and fail with a straight answer rather than letting
    # send_video throw something opaque.
    out_mb = rec.output.stat().st_size / (1024 * 1024)
    if out_mb > settings.max_outgoing_mb:
        tier = quality.get(rec.quality)
        # The most likely cause by far is a high tier, and the fix is one
        # button away — say so before mentioning the server-side workaround.
        hint = ("اضغط 🎚 الجودة واختر 1080p — الجودة العالية تنتج ملفات أكبر "
                "بكثير من حد تيليجرام."
                if tier.gated else
                "جرّب مقطعاً أقصر، أو شغّل Bot API server محلياً واضبط "
                "TELEGRAM_LOCAL_API=true.")
        raise RuntimeError(
            f"النسخة المعدّلة {out_mb:.0f} ميجابايت بجودة «{tier.label}»، "
            f"وحد الإرسال في تيليجرام {settings.max_outgoing_mb} ميجابايت.\n{hint}")

    # Probe the OUTPUT (not the source) so Telegram gets the correct dimensions
    # of the edited file — this is what stops vertical clips showing in a square
    # box and lets the phone offer "Save to Gallery".
    out_info = await probe(str(rec.output))
    width = out_info.get("width") or rec.probe.get("width") or 0
    height = out_info.get("height") or rec.probe.get("height") or 0
    duration = int(out_info.get("duration") or rec.probe.get("duration") or 0)

    # Generate a proper aspect-ratio thumbnail (no black padding).
    thumb_path = rec.work_dir / "thumb.jpg"
    thumb = None
    if await make_thumbnail(str(rec.output), str(thumb_path)):
        thumb = FSInputFile(str(thumb_path))

    # Remove the transient status message, then send the finished video.
    if status_message_id:
        try:
            await bot.delete_message(status_chat_id, status_message_id)
        except Exception:
            pass

    sent = await bot.send_video(
        chat_id=rec.chat_id,
        video=FSInputFile(str(rec.output)),
        caption=_caption(rec),
        parse_mode="Markdown",
        supports_streaming=True,
        # Telling Telegram the real width/height/duration fixes the "box" and
        # makes it a true saveable video instead of a generic file.
        width=width or None,
        height=height or None,
        duration=duration or None,
        thumbnail=thumb,
        reply_markup=result_keyboard(rec.id, rec.quality,
                                     users.is_admin(rec.user_id)),
    )
    rec.result_message_id = sent.message_id

    # Only now that the render succeeded and was delivered do we remember the
    # source hash — a failed/partial job must not poison future inputs.
    if settings.skip_duplicates and rec.source_hash:
        registry.remember(rec.source_hash, rec.user_id, rec.origin)

    log.info("job %s rendered in %.2fs (%s/%s) %dx%d for %s",
             rec.id, rec.last_render_seconds, rec.intensity, rec.quality,
             width, height, rec.user_id)
