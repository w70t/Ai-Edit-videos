"""
Who gets in, and who gets high quality.

Two routers with opposite audiences live here because they are two halves of
one feature:

  * `admin_router`    – the 👥 المستخدمون panel: approve, promote, revoke, remove.
  * `stranger_router` – the catch-all for anyone with no grant. It must be
    registered LAST, after every other router, so it only ever sees updates
    nobody else claimed.

The panel edits one message in place instead of posting a new one per tap —
managing five people should not bury the chat.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, or_f
from aiogram.types import CallbackQuery, Message

from ..config import settings
from ..filters import IsAdmin, IsStranger
from ..keyboards import (BTN_USERS, join_request_keyboard, main_menu,
                         pending_keyboard, user_detail_keyboard,
                         users_keyboard)
from ..services.users import registry

log = logging.getLogger(__name__)

admin_router = Router(name="access-admin")
admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())

stranger_router = Router(name="access-stranger")
stranger_router.message.filter(IsStranger())
stranger_router.callback_query.filter(IsStranger())


def _display_name(user) -> str:
    """A human label for a Telegram user, with the id as the last resort."""
    name = (user.full_name or "").strip()
    handle = f"@{user.username}" if user.username else ""
    return " ".join(p for p in (name, handle) if p) or str(user.id)


def _target_id(data: str) -> int:
    """Last segment of `usr:<action>:<id>` as an int, or 0."""
    try:
        return int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
#  The panel
# --------------------------------------------------------------------------- #
def _panel_text() -> str:
    users = registry.users()
    hq = sum(1 for u in users if u.hq)
    lines = [
        "👥 *المستخدمون*",
        "",
        f"• مسموح لهم: *{len(users)}*",
        f"• منهم بجودة عالية: *{hq}*",
        f"• طلبات معلّقة: *{len(registry.pending())}*",
        "",
        "الجميع يبدأ على *1080p*. الجودة العالية (2K / 4K) تُمنح فردياً — "
        "كل منح يعني احتمال حجز جهاز الرندر لوقت طويل.",
    ]
    if not users:
        lines.append("")
        lines.append("_لا أحد في القائمة بعد. أي شخص يراسل البوت سيظهر هنا "
                     "كطلب انضمام._")
    return "\n".join(lines)


async def _show_panel(message: Message) -> None:
    await message.answer(_panel_text(), parse_mode="Markdown",
                         reply_markup=users_keyboard(registry.users(),
                                                     len(registry.pending())))


@admin_router.message(or_f(Command("users"), F.text == BTN_USERS))
async def cmd_users(message: Message) -> None:
    await _show_panel(message)


@admin_router.callback_query(F.data == "usr:noop")
async def cb_noop(cq: CallbackQuery) -> None:
    """The name header rows in the pending list are labels, not buttons."""
    await cq.answer()


@admin_router.callback_query(F.data == "usr:close")
async def cb_close(cq: CallbackQuery) -> None:
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()


@admin_router.callback_query(F.data == "usr:list")
async def cb_list(cq: CallbackQuery) -> None:
    await cq.message.edit_text(
        _panel_text(), parse_mode="Markdown",
        reply_markup=users_keyboard(registry.users(), len(registry.pending())))
    await cq.answer()


@admin_router.callback_query(F.data == "usr:pend")
async def cb_pending(cq: CallbackQuery) -> None:
    pending = registry.pending()
    if not pending:
        await cq.answer("لا توجد طلبات معلّقة.", show_alert=True)
        return
    await cq.message.edit_text(
        "⏳ *طلبات الانضمام*\n\n"
        "«قبول 1080p» يعطيه الاستخدام العادي.\n"
        "«قبول + جودة عالية» يفتح له 2K و4K كمان.",
        parse_mode="Markdown",
        reply_markup=pending_keyboard(pending))
    await cq.answer()


@admin_router.callback_query(F.data.startswith("usr:u:"))
async def cb_detail(cq: CallbackQuery) -> None:
    entry = registry.get(_target_id(cq.data))
    if entry is None:
        await cb_list(cq)
        return
    await cq.message.edit_text(
        f"👤 *{entry.name or entry.id}*\n\n"
        f"• المعرّف: `{entry.id}`\n"
        f"• المستوى: {entry.tier_label}\n"
        f"• آخر جودة اختارها: {entry.quality}",
        parse_mode="Markdown",
        reply_markup=user_detail_keyboard(entry))
    await cq.answer()


# --------------------------------------------------------------------------- #
#  Decisions
# --------------------------------------------------------------------------- #
async def _notify(bot: Bot, uid: int, text: str) -> None:
    """Tell a user about a decision. They may have blocked the bot — shrug."""
    try:
        await bot.send_message(uid, text, parse_mode="Markdown",
                               reply_markup=main_menu(is_admin=False))
    except Exception as exc:
        log.info("could not notify %s: %s", uid, exc)


async def _approve(cq: CallbackQuery, bot: Bot, hq: bool) -> None:
    uid = _target_id(cq.data)
    if not uid:
        await cq.answer()
        return
    name = registry.pending_name(uid)
    registry.add(uid, name=name, hq=hq)
    log.info("admin approved %s (hq=%s)", uid, hq)

    await _notify(bot, uid,
                  "✅ تم قبولك — أرسل رابط ريلز/تيك توك أو ارفع فيديو.\n"
                  + ("⭐ عندك صلاحية *الجودة العالية*: اضغط 🎚 الجودة تحت "
                     "أي فيديو واختر 2K أو 4K."
                     if hq else
                     "الجودة عندك *1080p* — الأسرع، وتكفي لأغلب المقاطع."))
    await cq.answer(f"✅ {name} — {'جودة عالية' if hq else '1080p'}")
    await cb_list(cq)


@admin_router.callback_query(F.data.startswith("usr:okhq:"))
async def cb_approve_hq(cq: CallbackQuery, bot: Bot) -> None:
    await _approve(cq, bot, hq=True)


@admin_router.callback_query(F.data.startswith("usr:ok:"))
async def cb_approve(cq: CallbackQuery, bot: Bot) -> None:
    await _approve(cq, bot, hq=False)


@admin_router.callback_query(F.data.startswith("usr:no:"))
async def cb_deny(cq: CallbackQuery) -> None:
    uid = _target_id(cq.data)
    name = registry.pending_name(uid)
    registry.deny(uid)
    # Deliberately silent towards the applicant: a rejection notice invites an
    # argument, and they were never told a request was filed in the first place.
    await cq.answer(f"🚫 رُفض {name}")
    await cb_list(cq)


@admin_router.callback_query(F.data.startswith("usr:hq:"))
async def cb_toggle_hq(cq: CallbackQuery, bot: Bot) -> None:
    uid = _target_id(cq.data)
    entry = registry.get(uid)
    if entry is None:
        await cb_list(cq)
        return
    now_on = not entry.hq
    registry.set_hq(uid, now_on)
    await _notify(bot, uid,
                  "⭐ منحك الأدمن *الجودة العالية* — اضغط 🎚 الجودة تحت أي "
                  "فيديو واختر 2K أو 4K."
                  if now_on else
                  "ℹ️ رجعت جودتك إلى *1080p*.")
    await cq.answer("⭐ مُنحت الجودة العالية" if now_on else "⬇️ رجع إلى 1080p")
    await cb_detail(cq)


@admin_router.callback_query(F.data.startswith("usr:del:"))
async def cb_remove(cq: CallbackQuery) -> None:
    uid = _target_id(cq.data)
    entry = registry.get(uid)
    name = entry.name if entry else str(uid)
    registry.remove(uid)
    await cq.answer(f"🚫 أُزيل {name}")
    await cb_list(cq)


# --------------------------------------------------------------------------- #
#  Strangers
#
#  Registered last: anything reaching here was refused by every other router.
# --------------------------------------------------------------------------- #
@stranger_router.callback_query()
async def stranger_callback(cq: CallbackQuery) -> None:
    await cq.answer("⚠️ ما عندك صلاحية استخدام هذا البوت.", show_alert=True)


@stranger_router.message()
async def stranger_message(message: Message, bot: Bot) -> None:
    user = message.from_user
    name = _display_name(user)
    first_time = registry.request(user.id, name)

    await message.answer(
        "🔒 هذا البوت خاص.\n"
        "تم إرسال طلبك للأدمن — راح يوصلك رد إذا تمت الموافقة."
        if first_time else
        "🔒 طلبك قيد المراجعة عند الأدمن.")

    if not first_time:
        return

    log.info("join request from %s (%s)", user.id, name)
    try:
        await bot.send_message(
            settings.admin_id,
            f"👤 *طلب انضمام جديد*\n\n"
            f"• الاسم: {name}\n"
            f"• المعرّف: `{user.id}`\n\n"
            f"«قبول 1080p» للاستخدام العادي، و«قبول + جودة عالية» يفتح 2K/4K "
            f"(رندر أبطأ بكثير على الجهاز).",
            parse_mode="Markdown",
            reply_markup=join_request_keyboard(user.id))
    except Exception as exc:
        log.warning("could not reach admin about %s: %s", user.id, exc)
