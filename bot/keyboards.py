"""
Keyboard builders.

Two kinds live here:
  * the persistent *reply* keyboard — the always-visible menu under the input
    box, so nothing in day-to-day use requires typing a slash command,
  * the *inline* keyboards attached to a finished video.

Callback data format:  "<action>:<job_id>"  (sometimes "<action>:<job_id>:<arg>")
Keeping it compact matters — Telegram caps callback_data at 64 bytes.
"""

from __future__ import annotations

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from .services import quality as q
from .services.users import UserEntry

# --------------------------------------------------------------------------- #
#  Persistent menu
#
#  These strings are matched literally by the command handlers, so they are
#  defined once here and imported — never retyped.
# --------------------------------------------------------------------------- #
BTN_STATUS = "📊 الحالة"
BTN_RESEARCH = "🔎 بحث"
BTN_FORGET = "🧹 مسح سجل المكرر"
BTN_USERS = "👥 المستخدمون"
BTN_HELP = "ℹ️ مساعدة"

MENU_BUTTONS = (BTN_STATUS, BTN_RESEARCH, BTN_FORGET, BTN_USERS, BTN_HELP)
# Buttons only the owner ever sees. Handlers re-check permission anyway — a
# hidden button is a courtesy, not a security boundary.
ADMIN_ONLY_BUTTONS = (BTN_RESEARCH, BTN_FORGET, BTN_USERS)


def main_menu(is_admin: bool = True) -> ReplyKeyboardMarkup:
    """
    The always-on menu under the message box.

    `is_persistent` keeps it open instead of collapsing behind the little
    keyboard icon, which is the whole point — nobody should have to remember
    a command name. Guests get the two entries that are theirs to use.
    """
    kb = ReplyKeyboardBuilder()
    if is_admin:
        kb.row(KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_RESEARCH))
        kb.row(KeyboardButton(text=BTN_FORGET), KeyboardButton(text=BTN_USERS))
        kb.row(KeyboardButton(text=BTN_HELP))
    else:
        kb.row(KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_HELP))
    return kb.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل رابطاً أو فيديو…",
    )


def confirm_forget_keyboard() -> InlineKeyboardMarkup:
    """Clearing the registry is one tap away — make it two."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ نعم، امسح", callback_data="cfg:forget"),
        InlineKeyboardButton(text="↩️ إلغاء", callback_data="cfg:cancel"),
    )
    return kb.as_markup()


def result_keyboard(job_id: str, quality: str = q.STANDARD,
                    is_admin: bool = True,
                    can_hq: bool = True) -> InlineKeyboardMarkup:
    """
    The main control panel shown *under every finished video*.

    Layout:

        Row 0 – Repost preset (the one that actually moves a fingerprint)
            [Repost Mode]
        Row 1 – Edit Intensity
            [Light Edit] [Medium Edit] [Strong Edit]
        Row 2 – Quality — ONLY for the admin and granted users
            [Quality: 4K]
        Row 3 – Variants
            [Generate New Variant] [Try Different Settings]
        Row 4 – Actions (admin only: they write to the admin's disk/channel)
            [Save to Folder] [Forward to Channel] [Delete this version]
        Row 5 – Quick Options
            [Download Original] [Show Processing Info]

    An ordinary member never sees row 2 at all — not a locked button, not a
    hint. Their videos are 1080p and the tier system simply does not exist
    from where they stand.
    """
    kb = InlineKeyboardBuilder()

    # الصف 0 — وضع إعادة النشر (الوحيد الذي يجمع كل ما يكسر البصمة فعلاً)
    kb.row(
        InlineKeyboardButton(text="♻️ وضع إعادة النشر (موصى به)",
                             callback_data=f"edit:{job_id}:repost"),
    )

    # الصف 1 — شدة التعديل
    kb.row(
        InlineKeyboardButton(text="🟢 تعديل خفيف",  callback_data=f"edit:{job_id}:light"),
        InlineKeyboardButton(text="🟡 تعديل متوسط", callback_data=f"edit:{job_id}:medium"),
        InlineKeyboardButton(text="🔴 تعديل قوي",   callback_data=f"edit:{job_id}:strong"),
    )

    # الصف 2 — الجودة. لا تظهر إطلاقاً لغير المصرّح لهم.
    if can_hq:
        kb.row(
            InlineKeyboardButton(text=f"🎚 الجودة: {q.get(quality).label}",
                                 callback_data=f"qual:{job_id}"),
        )

    # الصف 3 — النسخ
    kb.row(
        InlineKeyboardButton(text="🎲 إنشاء نسخة جديدة", callback_data=f"variant:{job_id}"),
        InlineKeyboardButton(text="⚙️ إعدادات مختلفة",   callback_data=f"settings:{job_id}"),
    )

    # الصف 4 — الإجراءات. الحفظ والإرسال للقناة يكتبان في قرص الأدمن وقناته،
    # فلا يظهران لغيره.
    if is_admin:
        kb.row(
            InlineKeyboardButton(text="💾 حفظ في المجلد",   callback_data=f"save:{job_id}"),
            InlineKeyboardButton(text="📤 إرسال للقناة",     callback_data=f"forward:{job_id}"),
            InlineKeyboardButton(text="🗑 حذف هذه النسخة",   callback_data=f"delete:{job_id}"),
        )
    else:
        kb.row(
            InlineKeyboardButton(text="🗑 حذف هذه النسخة",   callback_data=f"delete:{job_id}"),
        )

    # الصف 5 — خيارات سريعة
    kb.row(
        InlineKeyboardButton(text="⬇️ تحميل الأصلي",     callback_data=f"original:{job_id}"),
        InlineKeyboardButton(text="ℹ️ معلومات المعالجة", callback_data=f"info:{job_id}"),
    )

    return kb.as_markup()


def quality_keyboard(job_id: str, current: str) -> InlineKeyboardMarkup:
    """
    The tier picker. Only ever reached by the admin and granted users, so every
    tier here is one they may actually choose — no locks to render.
    """
    kb = InlineKeyboardBuilder()
    current = q.get(current).key
    for key in q.ORDER:
        tier = q.TIERS[key]
        mark = "✅ " if key == current else ("⭐ " if tier.gated else "▫️ ")
        kb.row(InlineKeyboardButton(text=f"{mark}{tier.label}",
                                    callback_data=f"q:{job_id}:{key}"))
    kb.row(InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"back:{job_id}"))
    return kb.as_markup()


# --------------------------------------------------------------------------- #
#  User management (admin only)
# --------------------------------------------------------------------------- #
def users_keyboard(users: list[UserEntry], pending_count: int) -> InlineKeyboardMarkup:
    """The allowlist: one row per person, plus the two ways to add someone."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ إضافة شخص بالمعرّف (جودة عالية)",
                                callback_data="usr:add"))
    if pending_count:
        kb.row(InlineKeyboardButton(
            text=f"⏳ طلبات الانضمام ({pending_count})",
            callback_data="usr:pend"))
    for u in users:
        label = (u.name or str(u.id))[:24]
        kb.row(InlineKeyboardButton(text=f"👤 {label} · {u.tier_label}",
                                    callback_data=f"usr:u:{u.id}"))
    kb.row(InlineKeyboardButton(text="✖️ إغلاق", callback_data="usr:close"))
    return kb.as_markup()


def cancel_add_keyboard() -> InlineKeyboardMarkup:
    """A way out of the "send me an id" prompt that is not a typed command."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="↩️ إلغاء", callback_data="usr:cancel"))
    return kb.as_markup()


def user_detail_keyboard(user: UserEntry) -> InlineKeyboardMarkup:
    """Grant / revoke high quality, or remove the person entirely."""
    kb = InlineKeyboardBuilder()
    if user.hq:
        kb.row(InlineKeyboardButton(text="⬇️ سحب الجودة العالية (يرجع 1080p)",
                                    callback_data=f"usr:hq:{user.id}"))
    else:
        kb.row(InlineKeyboardButton(text="⭐ منح الجودة العالية",
                                    callback_data=f"usr:hq:{user.id}"))
    kb.row(InlineKeyboardButton(text="🚫 إزالة من القائمة",
                                callback_data=f"usr:del:{user.id}"))
    kb.row(InlineKeyboardButton(text="⬅️ رجوع", callback_data="usr:list"))
    return kb.as_markup()


def pending_keyboard(pending: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Everyone waiting for a decision, three taps each."""
    kb = InlineKeyboardBuilder()
    for uid, name in pending:
        kb.row(InlineKeyboardButton(text=f"— {name[:28]} —",
                                    callback_data="usr:noop"))
        kb.row(
            InlineKeyboardButton(text="✅ قبول 1080p", callback_data=f"usr:ok:{uid}"),
            InlineKeyboardButton(text="⭐ قبول + جودة عالية",
                                 callback_data=f"usr:okhq:{uid}"),
            InlineKeyboardButton(text="🚫 رفض", callback_data=f"usr:no:{uid}"),
        )
    kb.row(InlineKeyboardButton(text="⬅️ رجوع", callback_data="usr:list"))
    return kb.as_markup()


def join_request_keyboard(uid: int) -> InlineKeyboardMarkup:
    """Attached to the card the admin gets the moment a stranger writes in."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ قبول 1080p", callback_data=f"usr:ok:{uid}"),
        InlineKeyboardButton(text="⭐ قبول + جودة عالية",
                             callback_data=f"usr:okhq:{uid}"),
    )
    kb.row(InlineKeyboardButton(text="🚫 رفض", callback_data=f"usr:no:{uid}"))
    return kb.as_markup()


def settings_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """
    Sub-menu opened by "Try Different Settings". Lets the admin toggle the
    optional surgical tweaks before re-rendering, then go back.

    The 🔴/🟡/⚪ marks rate each toggle's effect on duplicate detection; the 🛡
    row is the opposite trade — those two guards protect reach by *reducing*
    evasion.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✂️ قص زمني 🔴",        callback_data=f"tog:{job_id}:trim"),
        InlineKeyboardButton(text="🔍 تقريب وتأطير 🔴",   callback_data=f"tog:{job_id}:zoom"),
    )
    kb.row(
        InlineKeyboardButton(text="🔊 طبقة الصوت 🔴",     callback_data=f"tog:{job_id}:pitch"),
        InlineKeyboardButton(text="↔️ عكس (مرآة) 🟡",     callback_data=f"tog:{job_id}:flip"),
    )
    kb.row(
        InlineKeyboardButton(text="🎨 تغيير الألوان ⚪",   callback_data=f"tog:{job_id}:color"),
    )
    # حماية الانتشار — تقلّل التخفّي عمداً مقابل إشارات الترتيب
    kb.row(
        InlineKeyboardButton(text="🎣 حماية الـ hook 🛡",
                             callback_data=f"tog:{job_id}:protect_hook"),
        InlineKeyboardButton(text="🎵 صوت رائج 🛡",
                             callback_data=f"tog:{job_id}:trending_audio"),
    )
    kb.row(
        InlineKeyboardButton(text="▶️ إعادة المعالجة الآن", callback_data=f"variant:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"back:{job_id}"),
    )
    return kb.as_markup()
