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
from ..utils.ffmpeg import probe
from .editor import edit_video
from .storage import JobRecord

log = logging.getLogger(__name__)


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
    extras = (" · " + "، ".join(flags)) if flags else ""
    label = {"light": "🟢 خفيف", "medium": "🟡 متوسط", "strong": "🔴 قوي"}.get(
        rec.intensity, rec.intensity)
    variant = f" · نسخة #{rec.variant_count}" if rec.variant_count else ""
    return f"✅ تم — تعديل *{label}*{extras}{variant}"


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
    )
    rec.output = result.output
    rec.last_render_seconds = round(time.monotonic() - started, 2)

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
        reply_markup=result_keyboard(rec.id),
    )
    rec.result_message_id = sent.message_id
    log.info("job %s rendered in %.2fs (%s)", rec.id, rec.last_render_seconds,
             rec.intensity)
