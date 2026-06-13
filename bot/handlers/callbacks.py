"""
Inline-keyboard button handlers.

callback_data convention:  "<action>:<job_id>[:<arg>]"

Re-renders are pushed back through the same job queue so the Pi never runs more
than MAX_CONCURRENT_JOBS FFmpeg processes at once.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile

from ..config import settings
from ..filters import IsAdmin
from ..keyboards import result_keyboard, settings_keyboard
from ..services.processor import render_and_send
from ..services.queue import Job, JobQueue
from ..services.storage import JobRecord, store
from ..utils.cleanup import remove_path

log = logging.getLogger(__name__)

router = Router(name="callbacks")
router.callback_query.filter(IsAdmin())


def _job_id(data: str) -> str:
    # "action:jobid[:arg]"
    parts = data.split(":")
    return parts[1] if len(parts) > 1 else ""


def _arg(data: str) -> str:
    parts = data.split(":")
    return parts[2] if len(parts) > 2 else ""


async def _require_job(cq: CallbackQuery) -> JobRecord | None:
    rec = store.get(_job_id(cq.data))
    if rec is None or rec.source is None or not rec.source.exists():
        await cq.answer("⚠️ This job expired — send the video again.", show_alert=True)
        return None
    return rec


async def _queue_rerender(cq: CallbackQuery, bot: Bot, queue: JobQueue,
                          rec: JobRecord, note: str) -> None:
    """Send a fresh status message and enqueue a re-render."""
    status = await bot.send_message(rec.chat_id, note)

    async def run() -> None:
        await render_and_send(
            bot, rec,
            status_chat_id=status.chat.id,
            status_message_id=status.message_id,
        )

    async def on_error(exc: Exception) -> None:
        try:
            await status.edit_text(f"❌ Re-render failed: {exc}")
        except Exception:
            pass

    await queue.submit(Job(id=f"{rec.id}-r{int(time.time())}", coro=run,
                           on_error=on_error))


# --------------------------------------------------------------------------- #
#  Row 1 — Edit Intensity
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    rec.intensity = _arg(cq.data) or rec.intensity
    rec.variant_count = 0
    await cq.answer(f"Re-rendering at {rec.intensity} intensity…")
    await _queue_rerender(cq, bot, queue, rec,
                          f"⚙️ Applying *{rec.intensity}* edit…")


# --------------------------------------------------------------------------- #
#  Row 2 — Variants
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("variant:"))
async def cb_variant(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    rec.variant_count += 1
    await cq.answer("Generating a fresh variant…")
    # Same intensity/options; the editor's internal randomness makes it unique.
    await _queue_rerender(cq, bot, queue, rec, "🎲 Generating new variant…")


@router.callback_query(F.data.startswith("settings:"))
async def cb_settings(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    o = rec.options
    state = (f"flip:{'on' if o.flip else 'off'}  "
             f"zoom:{'on' if o.zoom else 'off'}  "
             f"color:{'on' if o.color else 'off'}  "
             f"pitch:{'on' if o.pitch else 'off'}")
    await cq.message.edit_reply_markup(reply_markup=settings_keyboard(rec.id))
    await cq.answer(f"Current: {state}")


@router.callback_query(F.data.startswith("tog:"))
async def cb_toggle(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    which = _arg(cq.data)
    o = rec.options
    setattr(o, which, not getattr(o, which, False))
    await cq.answer(f"{which} → {'on' if getattr(o, which) else 'off'}")


@router.callback_query(F.data.startswith("back:"))
async def cb_back(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    await cq.message.edit_reply_markup(reply_markup=result_keyboard(rec.id))
    await cq.answer()


# --------------------------------------------------------------------------- #
#  Row 3 — Actions
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("save:"))
async def cb_save(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec or not rec.output or not rec.output.exists():
        await cq.answer("Nothing to save yet.", show_alert=True)
        return
    settings.save_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = settings.save_dir / f"{stamp}_{rec.intensity}_{rec.output.name}"
    shutil.copy2(rec.output, dest)
    await cq.answer(f"💾 Saved to {dest.name}", show_alert=True)


@router.callback_query(F.data.startswith("forward:"))
async def cb_forward(cq: CallbackQuery, bot: Bot) -> None:
    rec = await _require_job(cq)
    if not rec or not rec.output or not rec.output.exists():
        await cq.answer("Nothing to forward yet.", show_alert=True)
        return
    if not settings.forward_channel_id:
        await cq.answer("Set FORWARD_CHANNEL_ID in .env first.", show_alert=True)
        return
    try:
        await bot.send_video(
            chat_id=settings.forward_channel_id,
            video=FSInputFile(str(rec.output)),
            caption="",
            supports_streaming=True,
        )
        await cq.answer("📤 Forwarded to channel.")
    except Exception as exc:
        await cq.answer(f"Forward failed: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("delete:"))
async def cb_delete(cq: CallbackQuery) -> None:
    rec = store.get(_job_id(cq.data))
    # Delete the message holding the video + keyboard.
    try:
        await cq.message.delete()
    except Exception:
        pass
    if rec:
        remove_path(rec.work_dir)
        store.drop(rec.id)
    await cq.answer("🗑 Deleted.")


# --------------------------------------------------------------------------- #
#  Row 4 — Quick Options
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("original:"))
async def cb_original(cq: CallbackQuery, bot: Bot) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    await cq.answer("Sending the original…")
    await bot.send_document(
        chat_id=rec.chat_id,
        document=FSInputFile(str(rec.source)),
        caption="⬇️ Original (unedited) source",
    )


@router.callback_query(F.data.startswith("info:"))
async def cb_info(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    p = rec.probe or {}
    dur = p.get("duration", 0)
    info = (
        "ℹ️ *Processing info*\n"
        f"• Job: `{rec.id}`\n"
        f"• Source: {rec.origin[:60]}\n"
        f"• Resolution: {p.get('width', '?')}×{p.get('height', '?')}\n"
        f"• Duration: {dur:.1f}s\n"
        f"• Codec: {p.get('codec', '?')}\n"
        f"• Intensity: {rec.intensity}\n"
        f"• Variants made: {rec.variant_count}\n"
        f"• Last render: {rec.last_render_seconds:.2f}s\n"
        f"• HW accel: {settings.hw_accel}"
    )
    await cq.message.answer(info, parse_mode="Markdown")
    await cq.answer()
