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
#  Direct video uploads (video, or video sent as a document)
# --------------------------------------------------------------------------- #
def _is_video_doc(doc) -> bool:
    return bool(doc) and (doc.mime_type or "").startswith("video/")


@router.message(F.video | F.document.func(_is_video_doc))
async def handle_upload(message: Message, bot: Bot, queue: JobQueue) -> None:
    media = message.video or message.document
    size_mb = (media.file_size or 0) / (1024 * 1024)
    if size_mb > settings.max_video_mb:
        await message.answer(
            f"⚠️ That file is {size_mb:.0f} MB — over the "
            f"{settings.max_video_mb} MB limit.")
        return

    status = await message.answer("⬇️ Receiving your video…")
    rec = store.new(settings.work_dir, message.chat.id, origin="upload")
    dest = rec.work_dir / "source.mp4"
    try:
        await bot.download(media, destination=dest)
        rec.source = dest
        rec.intensity = settings.default_intensity
    except Exception as exc:
        log.warning("upload download failed: %s", exc)
        remove_path(rec.work_dir)
        store.drop(rec.id)
        await status.edit_text(
            f"❌ Couldn't receive the video.\n`{exc}`", parse_mode="Markdown")
        return

    await _enqueue_render(bot, queue, rec, status)
