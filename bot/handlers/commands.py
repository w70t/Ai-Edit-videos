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
from ..filters import IsAdmin, IsAllowed
from ..keyboards import (BTN_FORGET, BTN_HELP, BTN_RESEARCH, BTN_STATUS,
                         confirm_forget_keyboard, main_menu)
from ..services import quality
from ..services.dedup import registry
from ..services.queue import JobQueue
from ..services.research import research
from ..services.users import registry as users

router = Router(name="commands")
# Messages: anyone with a grant. Callbacks here only confirm a registry wipe,
# which is the admin's alone.
router.message.filter(IsAllowed())
router.callback_query.filter(IsAdmin())

ADMIN_ONLY = "⚠️ هذا الإجراء للأدمن فقط."

WELCOME_ADMIN = (
    "🎬 *بوت تعديل الفيديوهات*\n\n"
    "أرسل لي *رابط ريلز / تيك توك*، أو *ارفع فيديو*، أو *حوّل (forward) فيديو "
    "من أي قناة*، وراح أرجّع لك نسخة معدّلة مصمّمة لتفادي كشف المحتوى المكرر — "
    "مع لوحة أزرار للتحكم وتعديل النتيجة.\n\n"
    "🎚 *الجودة*: فيديوهاتك تنزل بأعلى جودة تلقائياً، وتقدر تغيّر المستوى من "
    "زر 🎚 تحت أي فيديو. الأعضاء العاديون على 1080p ثابت ولا يرون الزر أصلاً.\n"
    "👥 اضغط *المستخدمون* ← ➕ لإضافة شخص بمعرّفه ومنحه الجودة العالية.\n\n"
    "_تلميح: الملفات فوق 20 ميجابايت لا يسمح تيليجرام للبوتات بتحميلها — "
    "أرسل الرابط بدل الملف._"
)

WELCOME_GUEST = (
    "🎬 *بوت تعديل الفيديوهات*\n\n"
    "أرسل *رابط ريلز / تيك توك*، أو *ارفع فيديو*، وراح أرجّع لك نسخة معدّلة "
    "مع لوحة أزرار للتحكم.\n\n"
    "_الملفات فوق 20 ميجابايت لا يسمح تيليجرام للبوتات بتحميلها — أرسل "
    "الرابط بدل الملف._"
)


@router.message(or_f(Command("start", "help"), F.text == BTN_HELP))
async def cmd_start(message: Message) -> None:
    is_admin = users.is_admin(message.from_user.id)
    text = WELCOME_ADMIN if is_admin else WELCOME_GUEST
    if not is_admin and users.can_hq(message.from_user.id):
        text += ("\n\n⭐ عندك صلاحية *الجودة العالية* — اضغط 🎚 الجودة تحت أي "
                 "فيديو واختر 2K أو 4K.")
    # Sending the menu here is what installs it on the user's client.
    await message.answer(text, parse_mode="Markdown",
                         reply_markup=main_menu(is_admin))


@router.message(or_f(Command("status"), F.text == BTN_STATUS))
async def cmd_status(message: Message, queue: JobQueue) -> None:
    uid = message.from_user.id
    is_admin = users.is_admin(uid)
    lines = [
        "📊 *الحالة*",
        f"• مهام في الانتظار: *{queue.pending}*",
        f"• الوضع الافتراضي: *{settings.default_intensity}*",
    ]
    # Only surfaced to people who have a quality button to act on it with.
    if users.can_hq(uid):
        tier = quality.get(users.default_quality(uid))
        lines.insert(2, f"• جودتك الحالية: *{tier.label}*")
    if is_admin:
        dedup_state = "مُفعّل ✅" if settings.skip_duplicates else "معطّل ⛔"
        lines += [
            f"• تخطّي المكرر: {dedup_state} (مسجّل: *{registry.count()}*)",
            f"• المستخدمون: *{users.count()}* "
            f"(طلبات معلّقة: *{len(users.pending())}*)",
            f"• حدود تيليجرام: *{settings.max_incoming_mb}* ميجا استلام / "
            f"*{settings.max_outgoing_mb}* ميجا إرسال",
        ]
    lines.append("• البوت: يعمل ✅")
    await message.answer("\n".join(lines), parse_mode="Markdown",
                         reply_markup=main_menu(is_admin))


@router.message(or_f(Command("forget"), F.text == BTN_FORGET))
async def cmd_forget(message: Message) -> None:
    # The registry is shared across every user — only the owner may wipe it.
    if not users.is_admin(message.from_user.id):
        await message.answer(ADMIN_ONLY)
        return
    count = registry.count()
    if not count:
        await message.answer("🧹 السجل فارغ أصلاً — لا شيء لمسحه.",
                             reply_markup=main_menu(True))
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
    # Spends the admin's Tavily quota — not something a guest gets to trigger.
    if not users.is_admin(message.from_user.id):
        await message.answer(ADMIN_ONLY)
        return
    note = await message.answer("🔎 جارٍ البحث عن أحدث أساليب الكشف…")
    summary = await research()
    await note.edit_text(summary, parse_mode="Markdown",
                         disable_web_page_preview=True)
