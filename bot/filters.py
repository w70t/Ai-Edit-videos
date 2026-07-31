"""
Access gates.

Two filters, applied at the router level so nothing is protected by accident:

  * `IsAdmin`   – the owner alone. Guards anything that spends the admin's
    resources or touches shared state: the user list, the dedup registry, the
    save folder, the forward channel, the research API key.
  * `IsAllowed` – the owner plus everyone on the allowlist. Guards the actual
    video workflow.

`IsStranger` is the exact inverse of `IsAllowed`; it exists so the join-request
router can claim the updates the other routers refuse, instead of them falling
through into silence.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from .config import settings
from .services.users import registry


class IsAdmin(BaseFilter):
    """True only for the configured ADMIN_ID."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user) and user.id == settings.admin_id


class IsAllowed(BaseFilter):
    """True for the admin and for anyone the admin has added."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user) and registry.allowed(user.id)


class IsStranger(BaseFilter):
    """True for anyone with no grant at all."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user) and not registry.allowed(user.id)
