"""Регистрация пользователя: обновляет запись в БД и кладёт её в data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ..db import Repo


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        repo: Repo | None = data.get("repo")
        if tg_user is not None and repo is not None and not tg_user.is_bot:
            data["user"] = await repo.upsert_user(tg_user.id, tg_user.username, tg_user.first_name)
        return await handler(event, data)
