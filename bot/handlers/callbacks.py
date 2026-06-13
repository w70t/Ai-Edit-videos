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


# أسماء شدّة التعديل بالعربي للعرض.
INTENSITY_AR = {"light": "خفيف", "medium": "متوسط", "strong": "قوي"}
# أسماء الخيارات بالعربي.
OPTION_AR = {"flip": "العكس", "zoom": "التقريب",
             "color": "الألوان", "pitch": "طبقة الصوت"}


async def _require_job(cq: CallbackQuery) -> JobRecord | None:
    rec = store.get(_job_id(cq.data))
    if rec is None or rec.source is None or not rec.source.exists():
        await cq.answer("⚠️ انتهت صلاحية هذه المهمة — أرسل الفيديو من جديد.",
                        show_alert=True)
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
            await status.edit_text(f"❌ فشلت إعادة المعالجة: {exc}")
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
    label = INTENSITY_AR.get(rec.intensity, rec.intensity)
    await cq.answer(f"جارٍ إعادة المعالجة بشدّة {label}…")
    await _queue_rerender(cq, bot, queue, rec,
                          f"⚙️ جارٍ تطبيق تعديل *{label}*…")


# --------------------------------------------------------------------------- #
#  Row 2 — Variants
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("variant:"))
async def cb_variant(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    rec.variant_count += 1
    await cq.answer("جارٍ إنشاء نسخة جديدة…")
    # نفس الشدّة/الخيارات؛ العشوائية الداخلية في المحرّك تجعلها فريدة.
    await _queue_rerender(cq, bot, queue, rec, "🎲 جارٍ إنشاء نسخة جديدة…")


@router.callback_query(F.data.startswith("settings:"))
async def cb_settings(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    o = rec.options
    on, off = "مفعّل", "متوقف"
    state = (f"عكس:{on if o.flip else off}  "
             f"تقريب:{on if o.zoom else off}  "
             f"ألوان:{on if o.color else off}  "
             f"صوت:{on if o.pitch else off}")
    await cq.message.edit_reply_markup(reply_markup=settings_keyboard(rec.id))
    await cq.answer(f"الحالي: {state}")


@router.callback_query(F.data.startswith("tog:"))
async def cb_toggle(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    which = _arg(cq.data)
    o = rec.options
    setattr(o, which, not getattr(o, which, False))
    name = OPTION_AR.get(which, which)
    await cq.answer(f"{name} → {'مفعّل' if getattr(o, which) else 'متوقف'}")


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
        await cq.answer("لا يوجد شيء للحفظ بعد.", show_alert=True)
        return
    settings.save_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = settings.save_dir / f"{stamp}_{rec.intensity}_{rec.output.name}"
    shutil.copy2(rec.output, dest)
    await cq.answer(f"💾 تم الحفظ باسم {dest.name}", show_alert=True)


@router.callback_query(F.data.startswith("forward:"))
async def cb_forward(cq: CallbackQuery, bot: Bot) -> None:
    rec = await _require_job(cq)
    if not rec or not rec.output or not rec.output.exists():
        await cq.answer("لا يوجد شيء للإرسال بعد.", show_alert=True)
        return
    if not settings.forward_channel_id:
        await cq.answer("اضبط FORWARD_CHANNEL_ID في ملف .env أولاً.", show_alert=True)
        return
    try:
        await bot.send_video(
            chat_id=settings.forward_channel_id,
            video=FSInputFile(str(rec.output)),
            caption="",
            supports_streaming=True,
        )
        await cq.answer("📤 تم الإرسال إلى القناة.")
    except Exception as exc:
        await cq.answer(f"فشل الإرسال: {exc}", show_alert=True)


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
    await cq.answer("🗑 تم الحذف.")


# --------------------------------------------------------------------------- #
#  Row 4 — Quick Options
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("original:"))
async def cb_original(cq: CallbackQuery, bot: Bot) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    await cq.answer("جارٍ إرسال الفيديو الأصلي…")
    await bot.send_document(
        chat_id=rec.chat_id,
        document=FSInputFile(str(rec.source)),
        caption="⬇️ الفيديو الأصلي (بدون تعديل)",
    )


@router.callback_query(F.data.startswith("info:"))
async def cb_info(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    p = rec.probe or {}
    dur = p.get("duration", 0)
    label = INTENSITY_AR.get(rec.intensity, rec.intensity)
    info = (
        "ℹ️ *معلومات المعالجة*\n"
        f"• المهمة: `{rec.id}`\n"
        f"• المصدر: {rec.origin[:60]}\n"
        f"• الأبعاد: {p.get('width', '?')}×{p.get('height', '?')}\n"
        f"• المدة: {dur:.1f} ثانية\n"
        f"• الترميز: {p.get('codec', '?')}\n"
        f"• الشدّة: {label}\n"
        f"• عدد النسخ: {rec.variant_count}\n"
        f"• آخر معالجة: {rec.last_render_seconds:.2f} ثانية\n"
        f"• تسريع عتادي: {settings.hw_accel}"
    )
    await cq.message.answer(info, parse_mode="Markdown")
    await cq.answer()
