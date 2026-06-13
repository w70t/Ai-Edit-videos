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

    Layout (exactly as specified):

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

    # Row 1 — Edit Intensity
    kb.row(
        InlineKeyboardButton(text="🟢 Light Edit",  callback_data=f"edit:{job_id}:light"),
        InlineKeyboardButton(text="🟡 Medium Edit", callback_data=f"edit:{job_id}:medium"),
        InlineKeyboardButton(text="🔴 Strong Edit", callback_data=f"edit:{job_id}:strong"),
    )

    # Row 2 — Variants
    kb.row(
        InlineKeyboardButton(text="🎲 Generate New Variant", callback_data=f"variant:{job_id}"),
        InlineKeyboardButton(text="⚙️ Try Different Settings", callback_data=f"settings:{job_id}"),
    )

    # Row 3 — Actions
    kb.row(
        InlineKeyboardButton(text="💾 Save to Folder",      callback_data=f"save:{job_id}"),
        InlineKeyboardButton(text="📤 Forward to Channel",  callback_data=f"forward:{job_id}"),
        InlineKeyboardButton(text="🗑 Delete this version", callback_data=f"delete:{job_id}"),
    )

    # Row 4 — Quick Options
    kb.row(
        InlineKeyboardButton(text="⬇️ Download Original",    callback_data=f"original:{job_id}"),
        InlineKeyboardButton(text="ℹ️ Show Processing Info", callback_data=f"info:{job_id}"),
    )

    return kb.as_markup()


def settings_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """
    Sub-menu opened by "Try Different Settings". Lets the admin toggle the
    optional surgical tweaks before re-rendering, then go back.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="↔️ Mirror (flip)", callback_data=f"tog:{job_id}:flip"),
        InlineKeyboardButton(text="🔍 Zoom crop",     callback_data=f"tog:{job_id}:zoom"),
    )
    kb.row(
        InlineKeyboardButton(text="🎨 Color shift",   callback_data=f"tog:{job_id}:color"),
        InlineKeyboardButton(text="🔊 Pitch tweak",   callback_data=f"tog:{job_id}:pitch"),
    )
    kb.row(
        InlineKeyboardButton(text="▶️ Re-render now",  callback_data=f"variant:{job_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="⬅️ Back", callback_data=f"back:{job_id}"),
    )
    return kb.as_markup()
