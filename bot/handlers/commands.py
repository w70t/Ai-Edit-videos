"""
Basic command handlers.

Every action is reachable two ways: the persistent button menu (what the admin
actually uses) and the classic slash command (kept so the Telegram command menu
and muscle memory both still work). One handler serves both.

Note the `or_f(...)`: writing `Command(...) | (F.text == X)` looks equivalent
but is not. Python hands that `|` to magic_filter's `__ror__`, which treats the
Command object as a plain *value* to combine, producing a MagicFilter that
blows up with "unsupported operand type(s) for |: 'Command' and 'bool'" the
first time a message arrives. `or_f` builds a real _OrFilter.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.types import CallbackQuery, Message

from ..config import settings
from ..filters import IsAdmin
from ..keyboards import (BTN_FORGET, BTN_HELP, BTN_RESEARCH, BTN_STATUS,
                         confirm_forget_keyboard, main_menu)
from ..services.dedup import registry
from ..services.prefs import prefs
from ..services.queue import JobQueue
from ..services.research import research

router = Router(name="commands")
# Lock the whole router to the admin — both messages and callbacks.
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


WELCOME = (
    "🎬 *بوت تعديل الفيديوهات*\n\n"
    "أرسل لي *رابط ريلز / تيك توك*، أو *ارفع فيديو*، أو *حوّل (forward) فيديو "
    "من أي قناة*، وراح أرجّع لك نسخة معدّلة مصمّمة لتفادي كشف المحتوى المكرر — "
    "مع لوحة أزرار للتحكم وتعديل النتيجة.\n\n"
    "🛑 *ما أعدّل على الفيديو مباشرة*: أول ما يوصل الفيديو أسألك تختار الشدّة "
    "(♻️ إعادة نشر / 🔴 قوي / 🟡 متوسط / 🟢 خفيف)، وما يبدأ أي تعديل قبل "
    "اختيارك — وتقدر تلغي.\n\n"
    "👇 *استخدم الأزرار تحت* — ما تحتاج تكتب أي أمر ولا تعدّل أي ملف:\n"
    "• ⚙️ *الإعدادات* — تغيّر سلوك البوت بضغطة زر (يسأل قبل التعديل، تخطّي "
    "المكرر، الوضع الافتراضي).\n"
    "• ❔ *شرح الأزرار* — كل زر يشرح لك بالضبط وش يسوي.\n\n"
    "_تلميح: الملفات فوق 20 ميجابايت لا يسمح تيليجرام للبوتات بتحميلها — "
    "أرسل الرابط بدل الملف._"
)


@router.message(or_f(Command("start", "help"), F.text == BTN_HELP))
async def cmd_start(message: Message) -> None:
    # Sending the menu here is what installs it on the admin's client.
    await message.answer(WELCOME, parse_mode="Markdown", reply_markup=main_menu())


@router.message(or_f(Command("status"), F.text == BTN_STATUS))
async def cmd_status(message: Message, queue: JobQueue) -> None:
    dedup_state = "مُفعّل ✅" if prefs.skip_duplicates else "معطّل ⛔"
    ask_state = ("يسأل قبل التعديل ✅" if prefs.confirm_before_edit
                 else "يعدّل مباشرة ⚡")
    await message.answer(
        f"📊 *الحالة*\n"
        f"• مهام في الانتظار: *{queue.pending}*\n"
        f"• تخطّي المكرر: {dedup_state} (مسجّل: *{registry.count()}*)\n"
        f"• التأكيد: {ask_state}\n"
        f"• الوضع الافتراضي: *{prefs.default_intensity}*\n"
        f"• حدود تيليجرام: *{settings.max_incoming_mb}* ميجا استلام / "
        f"*{settings.max_outgoing_mb}* ميجا إرسال\n"
        f"• البوت: يعمل ✅",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


@router.message(or_f(Command("forget"), F.text == BTN_FORGET))
async def cmd_forget(message: Message) -> None:
    count = registry.count()
    if not count:
        await message.answer("🧹 السجل فارغ أصلاً — لا شيء لمسحه.",
                             reply_markup=main_menu())
        return
    # A button is easy to hit by accident; confirm before wiping the registry.
    await message.answer(
        f"⚠️ سيُمسح سجل تخطّي المكرر (*{count}* بصمة).\n"
        f"أي فيديو سبق إرساله سيُعالَج من جديد. متأكد؟",
        parse_mode="Markdown",
        reply_markup=confirm_forget_keyboard(),
    )


@router.callback_query(F.data == "cfg:forget")
async def cb_forget_confirm(cq: CallbackQuery) -> None:
    removed = registry.clear()
    await cq.message.edit_text(f"🧹 تم المسح — أُزيلت {removed} بصمة.")
    await cq.answer("تم المسح")


@router.callback_query(F.data == "cfg:cancel")
async def cb_forget_cancel(cq: CallbackQuery) -> None:
    await cq.message.edit_text("↩️ تم الإلغاء — السجل كما هو.")
    await cq.answer()


@router.message(or_f(Command("research"), F.text == BTN_RESEARCH))
async def cmd_research(message: Message) -> None:
    note = await message.answer("🔎 جارٍ البحث عن أحدث أساليب الكشف…")
    summary = await research()
    await note.edit_text(summary, parse_mode="Markdown",
                         disable_web_page_preview=True)
