"""
Inbound media handlers: links (Reels/TikTok) and direct video uploads.

Both paths converge on the same pattern:
  1. create a job record (the store builds a private temp dir),
  2. acquire the source (yt-dlp download, or download-from-Telegram),
  3. enqueue a render job so only N run at once on the Pi.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..config import settings
from ..filters import IsAllowed
from ..services import downloader, quality
from ..services.dedup import hash_file, registry
from ..services.processor import render_and_send
from ..services.queue import Job, JobQueue
from ..services.storage import JobRecord, store
from ..services.users import registry as users
from ..utils.cleanup import remove_path

log = logging.getLogger(__name__)

router = Router(name="media")
router.message.filter(IsAllowed())


def _too_big_message(size_mb: float) -> str:
    """
    Explain the Bot API size ceiling in terms the admin can act on.

    Used both by the up-front size gate and by the download error path: the
    gate relies on Telegram reporting file_size, which is not guaranteed for
    every attachment type, so the failure still has to explain itself.
    """
    # Legacy Markdown: bold is *one* asterisk. `**x**` renders as an empty bold
    # run followed by plain text, so it silently loses the emphasis.
    head = (f"حجم الملف {size_mb:.0f} ميجابايت — أكبر من" if size_mb > 0
            else "الملف أكبر من")
    lines = [
        f"⚠️ {head} حد تيليجرام للبوتات "
        f"({settings.max_incoming_mb} ميجابايت للاستلام).",
        "",
        "هذا حدّ تفرضه واجهة تيليجرام نفسها، ولا يوجد إعداد عندنا يرفعه.",
        "",
        "الحل:",
        "• أرسل *رابط* الفيديو بدل الملف — التحميل عبر الرابط لا يمرّ بهذا "
        "الحد إطلاقاً، ويصل لسقف الجودة المختارة.",
    ]
    if not settings.telegram_local_api:
        lines.append(
            f"• للمقاطع الطويلة (أكثر من ~دقيقتين) قد تتجاوز *النتيجة* حد "
            f"الإرسال ({settings.max_outgoing_mb} ميجابايت) أيضاً — عندها "
            f"تحتاج Bot API server محلياً مع `TELEGRAM_LOCAL_API=true`.")
    return "\n".join(lines)


async def _is_duplicate(rec: JobRecord, status_msg: Message) -> bool:
    """
    If duplicate-skipping is on and this exact source was processed before,
    drop the job, tell the admin, and return True. Otherwise stamp the hash on
    the record (it is committed to the registry only after a successful render)
    and return False.
    """
    if not settings.skip_duplicates or rec.source is None:
        return False

    # Hashing is blocking disk I/O — keep it off the event loop.
    digest = await asyncio.to_thread(hash_file, rec.source)
    prev = registry.seen(digest, rec.user_id)
    if prev is not None:
        remove_path(rec.work_dir)
        store.drop(rec.id)
        try:
            await status_msg.edit_text(
                "⏭️ تم التخطّي — هذا الفيديو سبق معالجته من قبل.\n"
                "أرسل /forget لمسح سجل التكرار إن أردت إعادة معالجته.")
        except Exception:
            pass
        return True

    rec.source_hash = digest
    return False


async def _enqueue_render(
    bot: Bot, queue: JobQueue, rec: JobRecord, status_msg: Message
) -> None:
    """Submit the heavy render to the queue with proper error reporting."""

    async def run() -> None:
        await render_and_send(
            bot, rec,
            status_chat_id=status_msg.chat.id,
            status_message_id=status_msg.message_id,
        )

    async def on_error(exc: Exception) -> None:
        remove_path(rec.work_dir)
        store.drop(rec.id)
        try:
            await status_msg.edit_text(f"❌ فشلت المعالجة: {exc}")
        except Exception:
            pass

    pos = await queue.submit(Job(id=rec.id, coro=run, on_error=on_error))
    if pos > 0:
        try:
            await status_msg.edit_text(
                f"🕓 في الانتظار (الترتيب {pos + 1}). سيبدأ قريباً…")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Links (text containing an http/https URL)
# --------------------------------------------------------------------------- #
@router.message(F.text.func(downloader.is_url))
async def handle_link(message: Message, bot: Bot, queue: JobQueue) -> None:
    url = downloader.extract_url(message.text)
    uid = message.from_user.id
    # Their remembered tier, already downgraded by the registry if the grant
    # was revoked since they last chose. A regular member is always 1080p and
    # is not told about tiers at all.
    tier = quality.get(users.default_quality(uid))
    suffix = f" ({tier.label})" if users.can_hq(uid) else ""
    status = await message.answer(f"⬇️ جارٍ تحميل الفيديو من الرابط…{suffix}")

    rec = store.new(settings.work_dir, message.chat.id, origin=url,
                    user_id=uid, quality=tier.key)
    try:
        rec.source = await downloader.download(url, rec.work_dir, rec.quality)
        rec.apply_preset(settings.default_intensity)
    except Exception as exc:
        log.warning("download failed: %s", exc)
        remove_path(rec.work_dir)
        store.drop(rec.id)
        # No parse_mode: yt-dlp errors quote URLs full of _ and *, and a
        # Markdown parse failure would replace this message with nothing.
        await status.edit_text(f"❌ تعذّر تحميل هذا الرابط.\n{exc}")
        return

    if await _is_duplicate(rec, status):
        return

    await _enqueue_render(bot, queue, rec, status)


# --------------------------------------------------------------------------- #
#  Video messages — works for BOTH direct uploads AND forwarded posts.
#
#  A forwarded message (e.g. a video forwarded from a channel) still carries the
#  same `message.video` / `video_note` / `animation` / document attachment, so
#  the very same handler picks it up. We accept:
#    • video            – normal video files
#    • video_note        – round "telescope" video messages
#    • animation         – GIFs / muted short clips
#    • document (video/*) – videos sent as files (uncompressed)
# --------------------------------------------------------------------------- #
def _is_video_doc(doc) -> bool:
    return bool(doc) and (doc.mime_type or "").startswith("video/")


def _pick_media(message: Message):
    """Return the first usable video-like attachment on the message, or None."""
    return (
        message.video
        or message.video_note
        or message.animation
        or (message.document if _is_video_doc(message.document) else None)
    )


# قيمة ثابتة تدل على أن الفيديو مرفوع مباشرة (وليس محوّلاً).
UPLOAD_ORIGIN = "رفع مباشر"


def _origin_label(message: Message) -> str:
    """يصف مصدر الفيديو (يُعرض في لوحة المعلومات)."""
    # aiogram 3.7+ توفّر بيانات التحويل عبر forward_origin.
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None:
            name = chat.title or chat.username or "قناة"
            return f"محوّل من {name}"
        sender = getattr(origin, "sender_user_name", None) or getattr(
            getattr(origin, "sender_user", None), "full_name", None)
        return f"محوّل من {sender}" if sender else "محوّل"
    return UPLOAD_ORIGIN


@router.message(
    F.video
    | F.video_note
    | F.animation
    | F.document.func(_is_video_doc)
)
async def handle_upload(message: Message, bot: Bot, queue: JobQueue) -> None:
    media = _pick_media(message)
    if media is None:
        return

    # Telegram's getFile caps what a bot may download at 20 MB on the public
    # API — well under MAX_VIDEO_MB. Check the real limit up front so an
    # oversized upload gets a straight answer instead of an opaque failure
    # halfway through bot.download().
    size_mb = (getattr(media, "file_size", 0) or 0) / (1024 * 1024)
    if size_mb > min(settings.max_video_mb, settings.max_incoming_mb):
        await message.answer(_too_big_message(size_mb), parse_mode="Markdown")
        return

    origin = _origin_label(message)
    verb = "جارٍ استيراد الفيديو المحوّل" if origin != UPLOAD_ORIGIN else "جارٍ استلام الفيديو"
    status = await message.answer(f"⬇️ {verb}…")

    uid = message.from_user.id
    # An upload cannot be re-fetched at a higher resolution — Telegram already
    # compressed it — so the tier here only steers the encode.
    rec = store.new(settings.work_dir, message.chat.id, origin=origin,
                    user_id=uid, quality=users.default_quality(uid))
    dest = rec.work_dir / "source.mp4"
    try:
        await bot.download(media, destination=dest)
        rec.source = dest
        rec.apply_preset(settings.default_intensity)
    except Exception as exc:
        log.warning("media download failed: %s", exc)
        remove_path(rec.work_dir)
        store.drop(rec.id)
        # Telegram reports the ceiling as a generic "file is too big" only once
        # the transfer is attempted — reachable whenever file_size was absent,
        # so translate it here instead of dumping the raw API error.
        if "too big" in str(exc).lower():
            await status.edit_text(_too_big_message(size_mb),
                                   parse_mode="Markdown")
        else:
            # No parse_mode: exception text may contain _ or *, which would
            # make Telegram reject the message itself.
            await status.edit_text(f"❌ تعذّر استلام الفيديو.\n{exc}")
        return

    if await _is_duplicate(rec, status):
        return

    await _enqueue_render(bot, queue, rec, status)
