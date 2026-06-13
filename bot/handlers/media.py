"""
Inbound media handlers: links (Reels/TikTok) and direct video uploads.

Both paths converge on the same pattern:
  1. create a job record (the store builds a private temp dir),
  2. acquire the source (yt-dlp download, or download-from-Telegram),
  3. enqueue a render job so only N run at once on the Pi.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..config import settings
from ..filters import IsAdmin
from ..services import downloader
from ..services.processor import render_and_send
from ..services.queue import Job, JobQueue
from ..services.storage import JobRecord, store
from ..utils.cleanup import remove_path

log = logging.getLogger(__name__)

router = Router(name="media")
router.message.filter(IsAdmin())


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
            await status_msg.edit_text(f"❌ Failed: {exc}")
        except Exception:
            pass

    pos = await queue.submit(Job(id=rec.id, coro=run, on_error=on_error))
    if pos > 0:
        try:
            await status_msg.edit_text(
                f"🕓 Queued (position {pos + 1}). Will start shortly…")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Links (text containing an http/https URL)
# --------------------------------------------------------------------------- #
@router.message(F.text.func(downloader.is_url))
async def handle_link(message: Message, bot: Bot, queue: JobQueue) -> None:
    url = downloader.extract_url(message.text)
    status = await message.answer("⬇️ Downloading source…")

    rec = store.new(settings.work_dir, message.chat.id, origin=url)
    try:
        rec.source = await downloader.download(
            url, rec.work_dir, settings.max_video_mb)
        rec.intensity = settings.default_intensity
    except Exception as exc:
        log.warning("download failed: %s", exc)
        remove_path(rec.work_dir)
        store.drop(rec.id)
        await status.edit_text(
            f"❌ Couldn't download that link.\n`{exc}`", parse_mode="Markdown")
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


def _origin_label(message: Message) -> str:
    """Describe where the video came from (for the info panel)."""
    # aiogram 3.7+ exposes forward metadata via forward_origin.
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None:
            name = chat.title or chat.username or "channel"
            return f"forwarded from {name}"
        sender = getattr(origin, "sender_user_name", None) or getattr(
            getattr(origin, "sender_user", None), "full_name", None)
        return f"forwarded from {sender}" if sender else "forwarded"
    return "upload"


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

    size_mb = (getattr(media, "file_size", 0) or 0) / (1024 * 1024)
    if size_mb > settings.max_video_mb:
        await message.answer(
            f"⚠️ That file is {size_mb:.0f} MB — over the "
            f"{settings.max_video_mb} MB limit.")
        return

    origin = _origin_label(message)
    verb = "Importing forwarded video" if origin != "upload" else "Receiving your video"
    status = await message.answer(f"⬇️ {verb}…")

    rec = store.new(settings.work_dir, message.chat.id, origin=origin)
    dest = rec.work_dir / "source.mp4"
    try:
        await bot.download(media, destination=dest)
        rec.source = dest
        rec.intensity = settings.default_intensity
    except Exception as exc:
        log.warning("media download failed: %s", exc)
        remove_path(rec.work_dir)
        store.drop(rec.id)
        await status.edit_text(
            f"❌ Couldn't receive the video.\n`{exc}`", parse_mode="Markdown")
        return

    await _enqueue_render(bot, queue, rec, status)
