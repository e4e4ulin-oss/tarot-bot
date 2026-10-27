"""Кто может сделать авто-расклад и на каком основании.

Правило простое: несколько первых раскладов бесплатны, дальше расклад
оплачивается. Решение вынесено сюда, а не в хендлер, потому что проверять его
придётся из нескольких мест, а менять — часто.

Сколько именно бесплатных, решает подписка на карту дня. Подписан — пять,
отключил — два. Потраченное при этом не возвращается: кто отписался на третьем
раскладе, остаётся ни с чем, и это не наказание, а та же арифметика — просто
полоса стала короче, чем он уже прошёл.

Бесплатных может быть и больше: за приглашённых друзей и за промокоды. Это не
вторая валюта, а то же самое число — полоса просто длиннее, и человеку видно
один остаток, а не два.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .db import Repo
from .db.models import Payment


@dataclass(frozen=True)
class Access:
    """Чем оплачен этот расклад и сколько бесплатных осталось."""

    allowed: bool
    reason: str  # owner | free | paid | needs_payment
    free_left: int
    payment: Payment | None = None
    # Сколько бесплатных всего положено сейчас и сколько осталось бы без
    # подписки: по этим двум числам человеку и объясняют, чем он рискует
    granted: int = 0
    without_daily: int = 0


def granted_to(settings: Settings, *, subscribed: bool, bonus: int = 0) -> int:
    """Сколько бесплатных раскладов положено человеку сейчас."""
    if not settings.free_needs_daily or subscribed:
        base = settings.free_auto_readings
    else:
        base = min(settings.free_without_daily, settings.free_auto_readings)
    return base + bonus


def owns(settings: Settings, user_id: int) -> bool:
    """Это владелец бота или его администратор.

    ADMIN_CHAT_ID в личной переписке совпадает с номером человека, поэтому
    владелец остаётся владельцем, даже если забыл вписать себя в ADMIN_IDS.
    Правило одно на всех — и для лимита раскладов, и для хозяйских экранов.
    """
    return settings.is_admin(user_id) or user_id == settings.admin_chat_id


async def check(repo: Repo, settings: Settings, user_id: int) -> Access:
    # Владелец бота и его администраторы раскладывают сколько нужно: лимит
    # придуман для клиентов, а им самим он мешает смотреть, как работает бот
    if owns(settings, user_id):
        return Access(True, "owner", free_left=0)

    used = await repo.count_auto_readings(user_id)
    user = await repo.get_user(user_id)
    bonus = user.bonus_readings if user else 0
    subscribed = await repo.is_daily_subscribed(user_id)

    granted = granted_to(settings, subscribed=subscribed, bonus=bonus)
    # Сколько осталось бы без подписки — нужно, чтобы предупредить до отключения
    without = granted_to(settings, subscribed=False, bonus=bonus)
    free_left = max(0, granted - used)

    if free_left:
        return Access(
            True, "free", free_left, granted=granted, without_daily=max(0, without - used)
        )

    payment = await repo.unspent_payment(user_id)
    if payment is not None:
        return Access(
            True, "paid", 0, payment, granted=granted, without_daily=max(0, without - used)
        )
    return Access(
        False, "needs_payment", 0, granted=granted, without_daily=max(0, without - used)
    )


async def spend(repo: Repo, access: Access) -> None:
    """Списывает оплату — только после того, как расклад состоялся."""
    if access.payment is not None:
        await repo.spend_payment(access.payment)
