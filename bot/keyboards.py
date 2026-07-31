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

# --------------------------------------------------------------------------- #
#  Persistent menu
#
#  These strings are matched literally by the command handlers, so they are
#  defined once here and imported — never retyped.
# --------------------------------------------------------------------------- #
BTN_STATUS = "📊 الحالة"
BTN_SETTINGS = "⚙️ الإعدادات"
BTN_GUIDE = "❔ شرح الأزرار"
BTN_RESEARCH = "🔎 بحث"
BTN_FORGET = "🧹 مسح سجل المكرر"
BTN_HELP = "ℹ️ مساعدة"

MENU_BUTTONS = (BTN_STATUS, BTN_SETTINGS, BTN_GUIDE, BTN_RESEARCH,
                BTN_FORGET, BTN_HELP)


def main_menu() -> ReplyKeyboardMarkup:
    """
    The always-on menu under the message box.

    `is_persistent` keeps it open instead of collapsing behind the little
    keyboard icon, which is the whole point — the admin should never have to
    remember a command name, or edit a file on the Pi to change a setting.
    """
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_SETTINGS))
    kb.row(KeyboardButton(text=BTN_GUIDE), KeyboardButton(text=BTN_RESEARCH))
    kb.row(KeyboardButton(text=BTN_FORGET), KeyboardButton(text=BTN_HELP))
    return kb.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل رابطاً أو فيديو…",
    )


# --------------------------------------------------------------------------- #
#  Settings panel  (⚙️ الإعدادات)
#
#  These edit the runtime prefs — the point is that no .env editing or service
#  restart is needed. The current values are passed in rather than imported so
#  this module stays a pure view layer.
# --------------------------------------------------------------------------- #
INTENSITY_BUTTON = {
    "repost": "♻️ إعادة نشر",
    "strong": "🔴 قوي",
    "medium": "🟡 متوسط",
    "light":  "🟢 خفيف",
}


def prefs_keyboard(confirm_before_edit: bool, skip_duplicates: bool,
                   default_intensity: str) -> InlineKeyboardMarkup:
    """Live state on the button faces — tapping one flips it immediately."""
    mark = lambda on: "✅ مفعّل" if on else "⛔ متوقف"      # noqa: E731

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text=f"🛑 يسأل قبل التعديل — {mark(confirm_before_edit)}",
        callback_data="pref:tog:confirm_before_edit"))
    kb.row(InlineKeyboardButton(
        text=f"⏭️ تخطّي الفيديو المكرر — {mark(skip_duplicates)}",
        callback_data="pref:tog:skip_duplicates"))

    # The preset pre-selected on the confirmation screen (and used directly
    # when "ask before editing" is off). The current one is ticked.
    kb.row(*[
        InlineKeyboardButton(
            text=(f"{label} ✅" if key == default_intensity else label),
            callback_data=f"pref:int:{key}")
        for key, label in INTENSITY_BUTTON.items()
    ])

    kb.row(
        InlineKeyboardButton(text="🧹 مسح سجل المكرر", callback_data="pref:forget"),
        InlineKeyboardButton(text="❔ شرح الإعدادات", callback_data="why:prefs"),
    )
    kb.row(InlineKeyboardButton(text="✖️ إغلاق", callback_data="pref:close"))
    return kb.as_markup()


# --------------------------------------------------------------------------- #
#  Button guide  (❔ شرح الأزرار)
#
#  Mirrors the real panels, but every button only *explains itself* — nothing
#  here touches a video. Keys match handlers/guide.py:EXPLAIN.
# --------------------------------------------------------------------------- #
GUIDE_ROWS: tuple[tuple[tuple[str, str], ...], ...] = (
    (("♻️ إعادة النشر", "repost"), ("🔴 قوي", "strong"),
     ("🟡 متوسط", "medium"), ("🟢 خفيف", "light")),
    (("🎲 نسخة جديدة", "variant"), ("⚙️ إعدادات مختلفة", "subsettings")),
    (("💾 حفظ في المجلد", "save"), ("📤 إرسال للقناة", "forward"),
     ("🗑 حذف هذه النسخة", "delete")),
    (("⬇️ تحميل الأصلي", "original"), ("ℹ️ معلومات المعالجة", "info")),
    (("✂️ قص زمني", "trim"), ("🔍 تقريب وتأطير", "zoom"),
     ("🔊 طبقة الصوت", "pitch")),
    (("↔️ عكس (مرآة)", "flip"), ("🎨 تغيير الألوان", "color")),
    (("🎣 حماية الـ hook", "protect_hook"), ("🎵 صوت رائج", "trending_audio")),
    (("🎬 شاشة التأكيد", "confirm"), ("❌ إلغاء", "cancel")),
    (("📊 الحالة", "status"), ("🔎 بحث", "research"), ("🧹 مسح السجل", "forget")),
    (("⚙️ الإعدادات", "prefs"), ("ℹ️ مساعدة", "help")),
)


def guide_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in GUIDE_ROWS:
        kb.row(*[InlineKeyboardButton(text=text, callback_data=f"why:{key}")
                 for text, key in row])
    return kb.as_markup()


def guide_back_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ رجوع لقائمة الشرح",
                                callback_data="why:index"))
    return kb.as_markup()


def confirm_forget_keyboard() -> InlineKeyboardMarkup:
    """Clearing the registry is one tap away — make it two."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ نعم، امسح", callback_data="cfg:forget"),
        InlineKeyboardButton(text="↩️ إلغاء", callback_data="cfg:cancel"),
    )
    return kb.as_markup()


def confirm_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """
    Shown *before* any editing happens, right after a video is received.

    Nothing is rendered until one of these is tapped — the whole point is that
    the admin picks the intensity per video instead of the bot silently
    applying DEFAULT_INTENSITY.

    Same layout language as `result_keyboard` so the two screens feel like one
    panel: recommended preset on top, the three intensities under it.
    """
    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(text="♻️ وضع إعادة النشر (موصى به)",
                             callback_data=f"go:{job_id}:repost"),
    )
    kb.row(
        InlineKeyboardButton(text="🟢 تعديل خفيف",  callback_data=f"go:{job_id}:light"),
        InlineKeyboardButton(text="🟡 تعديل متوسط", callback_data=f"go:{job_id}:medium"),
        InlineKeyboardButton(text="🔴 تعديل قوي",   callback_data=f"go:{job_id}:strong"),
    )
    kb.row(
        InlineKeyboardButton(text="⚙️ إعدادات مختلفة", callback_data=f"settings:{job_id}"),
        InlineKeyboardButton(text="❌ إلغاء",          callback_data=f"cancel:{job_id}"),
    )
    return kb.as_markup()


def result_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """
    The main control panel shown *under every finished video*.

    Layout:

        Row 0 – Repost preset (the one that actually moves a fingerprint)
            [Repost Mode]
        Row 1 – Edit Intensity
            [Light Edit] [Medium Edit] [Strong Edit]
        Row 2 – Variants
            [Generate New Variant] [Try Different Settings]
        Row 3 – Actions
            [Save to Folder] [Forward to Channel] [Delete this version]
        Row 4 – Quick Options
            [Download Original] [Show Processing Info]
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

    # الصف 2 — النسخ
    kb.row(
        InlineKeyboardButton(text="🎲 إنشاء نسخة جديدة", callback_data=f"variant:{job_id}"),
        InlineKeyboardButton(text="⚙️ إعدادات مختلفة",   callback_data=f"settings:{job_id}"),
    )

    # الصف 3 — الإجراءات
    kb.row(
        InlineKeyboardButton(text="💾 حفظ في المجلد",   callback_data=f"save:{job_id}"),
        InlineKeyboardButton(text="📤 إرسال للقناة",     callback_data=f"forward:{job_id}"),
        InlineKeyboardButton(text="🗑 حذف هذه النسخة",   callback_data=f"delete:{job_id}"),
    )

    # الصف 4 — خيارات سريعة
    kb.row(
        InlineKeyboardButton(text="⬇️ تحميل الأصلي",     callback_data=f"original:{job_id}"),
        InlineKeyboardButton(text="ℹ️ معلومات المعالجة", callback_data=f"info:{job_id}"),
    )

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
