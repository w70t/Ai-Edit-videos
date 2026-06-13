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
    "🎬 *AI Edit Videos Bot*\n\n"
    "Send me an *Instagram Reel / TikTok link* or *upload a video* and I'll "
    "return a lightly re-edited copy designed to dodge duplicate-content "
    "detection — then give you a control panel to tweak it.\n\n"
    "*Commands*\n"
    "• /start – this message\n"
    "• /status – queue & system info\n"
    "• /research – latest detection-method notes\n"
)


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME, parse_mode="Markdown")


@router.message(Command("status"))
async def cmd_status(message: Message, queue: JobQueue) -> None:
    pending = queue.pending
    await message.answer(
        f"📊 *Status*\n• Jobs waiting in queue: *{pending}*\n• Bot: online ✅",
        parse_mode="Markdown",
    )


@router.message(Command("research"))
async def cmd_research(message: Message) -> None:
    note = await message.answer("🔎 Researching latest detection methods…")
    summary = await research()
    await note.edit_text(summary, parse_mode="Markdown",
                         disable_web_page_preview=True)
