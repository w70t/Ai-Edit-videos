"""Basic command handlers: /start, /help, /status, /research."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..filters import IsAdmin
from ..services.queue import JobQueue
from ..services.research import research

router = Router(name="commands")
# Lock the whole router to the admin — both messages and callbacks.
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


WELCOME = (
    "🎬 *بوت تعديل الفيديوهات*\n\n"
    "أرسل لي *رابط ريلز / تيك توك*، أو *ارفع فيديو*، أو *حوّل (forward) فيديو "
    "من أي قناة*، وراح أرجّع لك نسخة معدّلة بشكل بسيط مصمّمة لتفادي كشف "
    "المحتوى المكرر — مع لوحة أزرار للتحكم وتعديل النتيجة.\n\n"
    "*الأوامر*\n"
    "• /start – هذه الرسالة\n"
    "• /status – حالة الطابور والنظام\n"
    "• /research – آخر أساليب الكشف عن التكرار\n"
)


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME, parse_mode="Markdown")


@router.message(Command("status"))
async def cmd_status(message: Message, queue: JobQueue) -> None:
    pending = queue.pending
    await message.answer(
        f"📊 *الحالة*\n• مهام في الانتظار: *{pending}*\n• البوت: يعمل ✅",
        parse_mode="Markdown",
    )


@router.message(Command("research"))
async def cmd_research(message: Message) -> None:
    note = await message.answer("🔎 جارٍ البحث عن أحدث أساليب الكشف…")
    summary = await research()
    await note.edit_text(summary, parse_mode="Markdown",
                         disable_web_page_preview=True)
