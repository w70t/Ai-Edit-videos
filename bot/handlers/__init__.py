"""Aggregate all routers so main.py can include them in one place."""

from aiogram import Router

from . import access, callbacks, commands, media


def build_router() -> Router:
    root = Router(name="root")
    # Admin-only user management first: its `usr:` callbacks must not be
    # shadowed by anything downstream.
    root.include_router(access.admin_router)
    root.include_router(commands.router)
    root.include_router(media.router)
    root.include_router(callbacks.router)
    # LAST, always. This one claims whatever the allow-listed routers refused,
    # turning silence into a join request.
    root.include_router(access.stranger_router)
    return root
