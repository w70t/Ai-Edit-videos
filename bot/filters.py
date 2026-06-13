"""
Admin gate.

A single reusable filter that lets exactly one Telegram user ID interact with
the bot. Applied at the router level so every handler is protected by default.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from .config import settings


class IsAdmin(BaseFilter):
    """True only for the configured ADMIN_ID."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user) and user.id == settings.admin_id
