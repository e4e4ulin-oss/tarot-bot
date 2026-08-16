"""Сборка роутеров. Порядок важен: fallback подключается последним."""

from aiogram import Router

from . import admin, author, auto, common

# Роутеры объявлены на уровне модулей, поэтому собрать корневой роутер можно один раз за процесс.
ROUTERS: tuple[Router, ...] = (
    common.router,
    admin.router,
    auto.router,
    author.router,
    common.fallback_router,
)


def build_router() -> Router:
    root = Router(name="root")
    for router in ROUTERS:
        root.include_router(router)
    return root


__all__ = ["ROUTERS", "build_router"]
