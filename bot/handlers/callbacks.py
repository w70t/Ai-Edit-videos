"""
Inline-keyboard button handlers.

callback_data convention:  "<action>:<job_id>[:<arg>]"

Re-renders are pushed back through the same job queue so the Pi never runs more
than MAX_CONCURRENT_JOBS FFmpeg processes at once.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile

from ..config import settings
from ..filters import IsAdmin
from ..keyboards import confirm_keyboard, result_keyboard, settings_keyboard
from ..services.editor import TOGGLEABLE, impact_label
from ..services.processor import submit_render
from ..services.queue import JobQueue
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
INTENSITY_AR = {"light": "خفيف", "medium": "متوسط", "strong": "قوي",
                "repost": "إعادة نشر"}
# أسماء الخيارات بالعربي.
OPTION_AR = {"flip": "العكس", "zoom": "التقريب والتأطير",
             "color": "الألوان", "pitch": "طبقة الصوت", "trim": "القص الزمني",
             "protect_hook": "حماية الـ hook", "trending_audio": "وضع الصوت الرائج"}


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
    # No drop_on_error: a failed re-render must leave the source in place so
    # the admin can go back and try different settings.
    await submit_render(bot, queue, rec, status.chat.id, status.message_id,
                        error_prefix="❌ فشلت إعادة المعالجة")


async def _start_first_render(cq: CallbackQuery, bot: Bot, queue: JobQueue,
                              rec: JobRecord, note: str) -> None:
    """
    Begin the *first* render of a job that has been waiting for approval.

    The confirmation message becomes the status message: editing it drops the
    buttons (so the choice can't be made twice) and the render pipeline then
    reuses that same message for progress, errors, and finally replaces it with
    the video.
    """
    rec.started = True
    try:
        await cq.message.edit_text(note, reply_markup=None)
    except Exception:
        pass
    await submit_render(bot, queue, rec, cq.message.chat.id,
                        cq.message.message_id, drop_on_error=True)


# --------------------------------------------------------------------------- #
#  Confirmation screen — shown before anything is edited
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("go:"))
async def cb_go(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    """The admin approved an intensity — only now does FFmpeg get to run."""
    rec = await _require_job(cq)
    if not rec:
        return
    if rec.started:
        await cq.answer("جارٍ العمل على هذه المهمة أصلاً…")
        return
    rec.apply_preset(_arg(cq.data) or settings.default_intensity)
    label = INTENSITY_AR.get(rec.intensity, rec.intensity)
    await cq.answer(f"بدأ التعديل بشدّة {label}…")
    await _start_first_render(cq, bot, queue, rec,
                              f"⚙️ جارٍ تطبيق تعديل {label}…")


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(cq: CallbackQuery) -> None:
    """Decline the video outright: no render, and the temp copy goes away."""
    rec = store.drop(_job_id(cq.data))
    if rec:
        remove_path(rec.work_dir)
    try:
        await cq.message.edit_text(
            "❌ تم الإلغاء — لم يُطبَّق أي تعديل، وحُذفت النسخة المؤقتة.",
            reply_markup=None)
    except Exception:
        pass
    await cq.answer("تم الإلغاء")


# --------------------------------------------------------------------------- #
#  Row 1 — Edit Intensity
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    # Picking a preset resets the option toggles to that preset's own set.
    rec.apply_preset(_arg(cq.data) or rec.intensity)
    rec.variant_count = 0
    label = INTENSITY_AR.get(rec.intensity, rec.intensity)
    await cq.answer(f"جارٍ إعادة المعالجة بشدّة {label}…")
    await _queue_rerender(cq, bot, queue, rec,
                          f"⚙️ جارٍ تطبيق تعديل {label}…")


# --------------------------------------------------------------------------- #
#  Row 2 — Variants
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("variant:"))
async def cb_variant(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    # This button doubles as "▶️ إعادة المعالجة الآن" in the settings sub-menu,
    # which the admin can also reach from the confirmation screen. There, no
    # render has happened yet — so it starts the first one instead of counting
    # a variant of a video that doesn't exist.
    if not rec.started:
        await cq.answer("بدأ التعديل…")
        await _start_first_render(cq, bot, queue, rec,
                                  "⚙️ جارٍ تنفيذ التعديل بالإعدادات المختارة…")
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
    state = (f"قص:{on if o.trim else off}  "
             f"تقريب:{on if o.zoom else off}  "
             f"صوت:{on if o.pitch else off}  "
             f"عكس:{on if o.flip else off}  "
             f"ألوان:{on if o.color else off}\n"
             f"🛡 hook:{on if o.protect_hook else off}  "
             f"صوت رائج:{on if o.trending_audio else off}")
    await cq.message.edit_reply_markup(reply_markup=settings_keyboard(rec.id))
    await cq.answer(f"الحالي:\n{state}", show_alert=True)


@router.callback_query(F.data.startswith("tog:"))
async def cb_toggle(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    which = _arg(cq.data)
    if which not in TOGGLEABLE:      # never setattr() a name we didn't define
        await cq.answer()
        return
    o = rec.options
    setattr(o, which, not getattr(o, which))
    name = OPTION_AR.get(which, which)
    await cq.answer(f"{name} → {'مفعّل' if getattr(o, which) else 'متوقف'}")


@router.callback_query(F.data.startswith("back:"))
async def cb_back(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    # "Back" has to return to whichever panel this sub-menu was opened from:
    # the confirmation screen (nothing rendered yet) or the result panel.
    panel = result_keyboard if rec.started else confirm_keyboard
    await cq.message.edit_reply_markup(reply_markup=panel(rec.id))
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
    dur = float(p.get("duration", 0) or 0)
    label = INTENSITY_AR.get(rec.intensity, rec.intensity)
    lines = [
        "ℹ️ معلومات المعالجة",
        f"• المهمة: {rec.id}",
        f"• المصدر: {rec.origin[:60]}",
        f"• الأبعاد: {p.get('width', '?')}×{p.get('height', '?')}",
        f"• المدة الأصلية: {dur:.1f} ثانية",
        f"• الترميز: {p.get('codec', '?')}",
        f"• الشدّة: {label}",
        f"• عدد النسخ: {rec.variant_count}",
        f"• آخر معالجة: {rec.last_render_seconds:.2f} ثانية",
        f"• تسريع عتادي: {settings.hw_accel}",
    ]
    if rec.edit_notes:
        lines.append("")
        lines.append(f"🎯 الأثر المتوقع: {impact_label(rec.edit_notes)}")
        lines.append("ما طُبِّق فعلياً:")
        lines.extend(f"  {n}" for n in rec.edit_notes)
        lines.append("")
        lines.append("🔴 يزيح البصمة الإدراكية · 🟡 جزئي · ⚪ تجميلي فقط")
    # NOTE: no parse_mode — rec.origin is a raw URL and TikTok/Instagram links
    # routinely contain _ and *, which make Telegram reject a Markdown message.
    await cq.message.answer("\n".join(lines))
    await cq.answer()
