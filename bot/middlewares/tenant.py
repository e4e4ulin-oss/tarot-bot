"""Мидлварь, которая определяет, чьему боту адресовано обновление.

Один процесс обслуживает несколько ботов, и обработчикам нужно видеть
настройки того бренда, в чей бот написали. Подмена происходит здесь: дальше
по цепочке в data["settings"] лежит обычный Settings, просто собранный под
этого арендатора.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy import select

from ..config import Settings
from ..db.models import Tenant
from ..tenants import settings_for

logger = logging.getLogger(__name__)


class TenantMiddleware(BaseMiddleware):
    def __init__(self, base: Settings) -> None:
        self.base = base

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        bot = data.get("bot")
        if session is None or bot is None:  # pragma: no cover - страховка
            return await handler(event, data)

        tenant = await session.scalar(select(Tenant).where(Tenant.bot_id == bot.id))
        if tenant is None:
            # Бот работает, а арендатора нет: лучше промолчать, чем отвечать
            # чужими ценами и писать чужому администратору
            logger.warning("Обновление от бота %s без арендатора — пропускаю", bot.id)
            return None
        if not tenant.active:
            return None

        data["tenant"] = tenant
        data["settings"] = settings_for(tenant, self.base)
        return await handler(event, data)
