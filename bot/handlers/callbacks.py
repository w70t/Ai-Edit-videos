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
from ..filters import IsAllowed
from ..keyboards import (quality_keyboard, result_keyboard, settings_keyboard)
from ..services import quality
from ..services.editor import TOGGLEABLE, impact_label
from ..services.processor import refetch_source, render_and_send
from ..services.queue import Job, JobQueue
from ..services.storage import JobRecord, store
from ..services.users import registry as users
from ..utils.cleanup import remove_path

log = logging.getLogger(__name__)

router = Router(name="callbacks")
router.callback_query.filter(IsAllowed())


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
    """
    Resolve the job behind a button press, and check it belongs to the presser.

    Ownership matters now that more than one person uses the bot: job ids ride
    in callback_data, and a guessed id must not hand someone else's video —
    or their delete button — to a stranger. The admin is exempt so they can
    still help with any job.
    """
    rec = store.get(_job_id(cq.data))
    if rec is None or rec.source is None or not rec.source.exists():
        await cq.answer("⚠️ انتهت صلاحية هذه المهمة — أرسل الفيديو من جديد.",
                        show_alert=True)
        return None
    presser = cq.from_user.id
    if rec.user_id and rec.user_id != presser and not users.is_admin(presser):
        await cq.answer("⚠️ هذه المهمة ليست لك.", show_alert=True)
        return None
    return rec


async def _queue_rerender(cq: CallbackQuery, bot: Bot, queue: JobQueue,
                          rec: JobRecord, note: str,
                          refetch: bool = False) -> None:
    """
    Send a fresh status message and enqueue a re-render.

    `refetch` re-downloads the source first — needed only when the quality tier
    changed on a link job, and done inside the queue so a 4K pull cannot run
    concurrently with somebody else's render.
    """
    status = await bot.send_message(rec.chat_id, note)

    async def run() -> None:
        if refetch:
            await refetch_source(rec)
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
    # Picking a preset resets the option toggles to that preset's own set.
    rec.apply_preset(_arg(cq.data) or rec.intensity)
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


# --------------------------------------------------------------------------- #
#  Quality tier
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("qual:"))
async def cb_quality_menu(cq: CallbackQuery) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    can_hq = users.can_hq(cq.from_user.id)
    await cq.message.edit_reply_markup(
        reply_markup=quality_keyboard(rec.id, rec.quality, can_hq))

    lines = [f"{quality.TIERS[k].label} — {quality.TIERS[k].note}"
             for k in quality.ORDER]
    if not can_hq:
        lines.append("")
        lines.append("🔒 المستويات فوق 1080p تحتاج إذن من الأدمن.")
    elif not settings.telegram_local_api:
        # The one failure mode that actually bites: the render succeeds and
        # then cannot be delivered. Say it before they pick, not after.
        lines.append("")
        lines.append(f"⚠️ حد الإرسال {settings.max_outgoing_mb} ميجابايت — "
                     f"المقاطع الطويلة بجودة عالية قد تتجاوزه.")
    await cq.answer("\n".join(lines), show_alert=True)


@router.callback_query(F.data.startswith("q:"))
async def cb_quality_set(cq: CallbackQuery, bot: Bot, queue: JobQueue) -> None:
    rec = await _require_job(cq)
    if not rec:
        return
    tier = quality.get(_arg(cq.data))

    if tier.gated and not users.can_hq(cq.from_user.id):
        await cq.answer(
            f"🔒 «{tier.label}» متاح للأدمن والأشخاص المصرّح لهم فقط.\n"
            f"اطلب من الأدمن منحك الجودة العالية.", show_alert=True)
        return

    if tier.key == rec.quality:
        await cq.answer("هذه الجودة مطبّقة أصلاً.")
        return

    rec.quality = tier.key
    # Remember it so their next video starts here without another tap.
    users.set_quality(cq.from_user.id, tier.key)
    await cq.message.edit_reply_markup(
        reply_markup=result_keyboard(rec.id, rec.quality,
                                     users.is_admin(cq.from_user.id)))

    if rec.from_link:
        await cq.answer(f"🎚 {tier.label} — جارٍ إعادة التحميل بهذه الجودة…")
        await _queue_rerender(
            cq, bot, queue, rec,
            f"⬇️ جارٍ إعادة تحميل المصدر بجودة *{tier.label}*…",
            refetch=True)
        return

    # An upload has no higher-resolution original to go back for; only the
    # encode improves. Be explicit rather than implying a new download.
    await cq.answer(
        f"🎚 {tier.label} — الفيديو مرفوع مباشرة، فلا يمكن جلب دقة أعلى؛ "
        f"سيُعاد الترميز بجودة أفضل فقط.", show_alert=True)
    await _queue_rerender(cq, bot, queue, rec,
                          f"⚙️ جارٍ إعادة الترميز بجودة *{tier.label}*…")


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
    await cq.message.edit_reply_markup(
        reply_markup=result_keyboard(rec.id, rec.quality,
                                     users.is_admin(cq.from_user.id)))
    await cq.answer()


# --------------------------------------------------------------------------- #
#  Row 3 — Actions
# --------------------------------------------------------------------------- #
async def _admin_only(cq: CallbackQuery) -> bool:
    """
    Guard the two actions that touch the ADMIN's own resources.

    The buttons are already hidden from guests, but callback_data is just text
    a client can send — the check has to live here too.
    """
    if users.is_admin(cq.from_user.id):
        return True
    await cq.answer("⚠️ هذا الإجراء للأدمن فقط.", show_alert=True)
    return False


@router.callback_query(F.data.startswith("save:"))
async def cb_save(cq: CallbackQuery) -> None:
    if not await _admin_only(cq):
        return
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
    if not await _admin_only(cq):
        return
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
    # Not routed through _require_job: deleting must still work once the source
    # file is gone. The ownership half of that check is still required though.
    presser = cq.from_user.id
    if rec and rec.user_id and rec.user_id != presser and not users.is_admin(presser):
        await cq.answer("⚠️ هذه المهمة ليست لك.", show_alert=True)
        return
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
    tier = quality.get(rec.quality)
    lines = [
        "ℹ️ معلومات المعالجة",
        f"• المهمة: {rec.id}",
        f"• المصدر: {rec.origin[:60]}",
        f"• الأبعاد: {p.get('width', '?')}×{p.get('height', '?')}",
        f"• المدة الأصلية: {dur:.1f} ثانية",
        f"• الترميز: {p.get('codec', '?')}",
        f"• الشدّة: {label}",
        f"• الجودة: {tier.label} (CRF {tier.crf} · {tier.preset} · "
        f"صوت {tier.audio_bitrate})",
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
