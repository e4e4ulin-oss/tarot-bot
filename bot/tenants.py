"""Арендаторы: как из строки в базе получаются настройки конкретного бота.

Сервис один, ботов много. Всё, чем бренды отличаются, лежит в таблице
арендаторов; обработчикам об этом знать незачем — им по-прежнему приходит
обычный Settings, просто собранный под того бота, которому адресовано
обновление.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .db.models import Tenant

logger = logging.getLogger(__name__)

# Что арендатор перекрывает в настройках процесса. Остальное — ключи моделей,
# адрес базы, таймауты — общее для всех и живёт в окружении.
def overrides(tenant: Tenant) -> dict[str, object]:
    return {
        "bot_token": tenant.bot_token,
        "admin_chat_id": tenant.admin_chat_id,
        "admin_ids": list(tenant.admin_ids or []),
        "author_name": tenant.author_name,
        "author_currency": tenant.currency,
        "author_price_short": tenant.price_short,
        "author_price_base": tenant.price_base,
        "author_price_full": tenant.price_full,
        "free_auto_readings": tenant.free_auto_readings,
        "free_needs_daily": tenant.free_needs_daily,
        "free_without_daily": tenant.free_without_daily,
        "free_daily_limit": tenant.free_daily_limit,
        "auto_reading_price": tenant.auto_reading_price,
        "natal_reading_price": tenant.natal_reading_price,
        "auto_reading_stars": tenant.auto_reading_stars,
        "natal_reading_stars": tenant.natal_reading_stars,
        "payment_method": tenant.payment_method,
        "payment_provider_token": tenant.payment_provider_token,
        "miniapp_url": tenant.miniapp_url or Settings.model_fields["miniapp_url"].default,
    }


def settings_for(tenant: Tenant, base: Settings) -> Settings:
    """Настройки процесса, перекрытые тем, что принадлежит арендатору."""
    return base.model_copy(update=overrides(tenant))


def bot_id_of(token: str) -> int:
    """Числовая часть токена. Telegram кладёт её же в Bot.id."""
    return int(token.split(":", 1)[0])


async def active_tenants(session: AsyncSession) -> list[Tenant]:
    result = await session.execute(select(Tenant).where(Tenant.active).order_by(Tenant.id))
    return list(result.scalars())


async def ensure_default(session: AsyncSession, settings: Settings) -> Tenant | None:
    """Поднимает нынешнего бота в арендатора, если арендаторов ещё нет.

    Без этого первый же запуск после обновления оставил бы работающего бота
    без настроек: таблица пустая, значит и ботов нет.
    """
    existing = await session.execute(select(Tenant).limit(1))
    if existing.scalar_one_or_none() is not None:
        return None
    if not settings.bot_token:
        return None

    tenant = Tenant(
        slug="default",
        title=settings.author_name,
        bot_token=settings.bot_token,
        bot_id=bot_id_of(settings.bot_token),
        admin_chat_id=settings.admin_chat_id,
        admin_ids=list(settings.admin_ids),
        author_name=settings.author_name,
        currency=settings.author_currency,
        price_short=settings.author_price_short,
        price_base=settings.author_price_base,
        price_full=settings.author_price_full,
        free_auto_readings=settings.free_auto_readings,
        free_needs_daily=settings.free_needs_daily,
        auto_reading_price=settings.auto_reading_price,
        natal_reading_price=settings.natal_reading_price,
        auto_reading_stars=settings.auto_reading_stars,
        natal_reading_stars=settings.natal_reading_stars,
        payment_method=settings.payment_method,
        payment_provider_token=settings.payment_provider_token,
        miniapp_url=settings.miniapp_url,
    )
    session.add(tenant)
    await session.commit()
    logger.info("Нынешний бот заведён арендатором «%s»", tenant.slug)
    return tenant
