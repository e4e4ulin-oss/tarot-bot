"""Простой антифлуд: не чаще одного действия в N секунд на пользователя."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..texts import TOO_FAST


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.7) -> None:
        self.rate = rate
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        previous = self._last.get(user.id, 0.0)
        if now - previous < self.rate:
            if isinstance(event, CallbackQuery):
                await event.answer(TOO_FAST)
            elif isinstance(event, Message):
                pass  # сообщения просто игнорируем, чтобы не спамить в ответ
            return None

        self._last[user.id] = now
        return await handler(event, data)
