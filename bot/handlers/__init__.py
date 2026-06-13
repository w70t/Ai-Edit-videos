"""Aggregate all routers so main.py can include them in one place."""

from aiogram import Router

from . import callbacks, commands, media


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(commands.router)
    root.include_router(media.router)
    root.include_router(callbacks.router)
    return root
