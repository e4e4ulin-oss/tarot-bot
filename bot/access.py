"""Кто может сделать авто-расклад и на каком основании.

Правило простое: несколько первых раскладов бесплатны тем, кто подписан на
карту дня, дальше расклад оплачивается. Решение вынесено сюда, а не в хендлер,
потому что проверять его придётся из нескольких мест, а менять — часто.
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
    reason: str  # free | paid | needs_daily | needs_payment
    free_left: int
    payment: Payment | None = None


async def check(repo: Repo, settings: Settings, user_id: int) -> Access:
    used = await repo.count_auto_readings(user_id)
    free_left = max(0, settings.free_auto_readings - used)

    if free_left:
        if settings.free_needs_daily and not await repo.is_daily_subscribed(user_id):
            return Access(False, "needs_daily", free_left)
        return Access(True, "free", free_left)

    payment = await repo.unspent_payment(user_id)
    if payment is not None:
        return Access(True, "paid", 0, payment)
    return Access(False, "needs_payment", 0)


async def spend(repo: Repo, access: Access) -> None:
    """Списывает оплату — только после того, как расклад состоялся."""
    if access.payment is not None:
        await repo.spend_payment(access.payment)
