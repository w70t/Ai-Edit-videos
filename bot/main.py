"""
Application entrypoint.

  python -m bot.main

Wires together config, the job queue, the background temp sweeper, and the
aiogram dispatcher, then long-polls Telegram. Dependencies (bot, queue) are
injected into every handler via the dispatcher's workflow data.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from .config import settings
from .handlers import build_router
from .services.prefs import prefs
from .services.queue import JobQueue
from .utils.cleanup import periodic_sweep
from .utils.ffmpeg import (available_hwaccels, ffmpeg_available,
                           has_v4l2_decoder, resolve_hwaccel)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
    settings.validate()
    settings.ensure_dirs()

    if not ffmpeg_available():
        raise SystemExit("FFmpeg/ffprobe not found. Install with: sudo apt install -y ffmpeg")

    log.info("admin id: %s", settings.admin_id)
    log.info("work dir: %s | save dir: %s", settings.work_dir, settings.save_dir)
    # Printed on every boot so a stale deployment is obvious in the logs: the
    # old build has no such line at all.
    log.info("prefs: ask-before-edit=%s | default=%s | skip-duplicates=%s (file: %s)",
             prefs.confirm_before_edit, prefs.default_intensity,
             prefs.skip_duplicates, settings.prefs_db)
    log.info("telegram limits: %d MB in / %d MB out%s",
             settings.max_incoming_mb, settings.max_outgoing_mb,
             " (local Bot API server)" if settings.telegram_local_api else "")

    # Resolve hardware decode ONCE here, so a machine with no usable path never
    # pays for a failed render plus a software retry on every job.
    log.info("hw accel: setting=%s | ffmpeg offers: %s | v4l2m2m decoder built in: %s",
             settings.hw_accel,
             ", ".join(sorted(available_hwaccels())) or "none",
             has_v4l2_decoder())
    await resolve_hwaccel(settings.hw_accel)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )

    # Job queue: caps concurrent FFmpeg renders.
    queue = JobQueue(concurrency=settings.max_concurrent_jobs)
    queue.start()

    # Background temp sweeper.
    sweeper = asyncio.create_task(periodic_sweep(settings.work_dir))

    # Dispatcher with injected dependencies (available as handler kwargs).
    dp = Dispatcher()
    dp["queue"] = queue
    dp.include_router(build_router())

    try:
        # Drop any backlog accumulated while the bot was offline.
        await bot.delete_webhook(drop_pending_updates=True)

        # Populate Telegram's native "/" menu. The button keyboard is the
        # primary interface; this just keeps commands discoverable too.
        await bot.set_my_commands([
            BotCommand(command="start", description="البداية وإظهار الأزرار"),
            BotCommand(command="status", description="حالة الطابور والنظام"),
            BotCommand(command="settings", description="الإعدادات (بدون ملفات)"),
            BotCommand(command="guide", description="شرح كل زر ووش يسوي"),
            BotCommand(command="research", description="آخر أساليب كشف التكرار"),
            BotCommand(command="forget", description="مسح سجل تخطّي المكرر"),
        ])

        log.info("bot is up — polling for updates")
        await dp.start_polling(bot, queue=queue)
    finally:
        sweeper.cancel()
        await queue.stop()
        await bot.session.close()
        log.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
