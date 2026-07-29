"""
Inline keyboard builders.

Callback data format:  "<action>:<job_id>"  (sometimes "<action>:<job_id>:<arg>")
Keeping it compact matters — Telegram caps callback_data at 64 bytes.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
    kb.row(
        InlineKeyboardButton(text="▶️ إعادة المعالجة الآن", callback_data=f"variant:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"back:{job_id}"),
    )
    return kb.as_markup()
