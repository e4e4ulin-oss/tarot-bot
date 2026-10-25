"""Кто может сделать авто-расклад и на каком основании.

Правило простое: несколько первых раскладов бесплатны тем, кто подписан на
карту дня, дальше расклад оплачивается. Решение вынесено сюда, а не в хендлер,
потому что проверять его придётся из нескольких мест, а менять — часто.

Бесплатных раскладов может быть больше обычного: за приглашённых друзей и за
промокоды. Это не вторая валюта, а то же самое число — бесплатная полоса просто
длиннее, и человеку видно один остаток, а не два.
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
    reason: str  # owner | free | paid | needs_daily | needs_payment
    free_left: int
    payment: Payment | None = None


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
    # Расклады за приглашённых и промокоды просто продлевают бесплатную полосу:
    # отдельной валюты заводить незачем, а остаток человеку виден одним числом
    user = await repo.get_user(user_id)
    granted = settings.free_auto_readings + (user.bonus_readings if user else 0)
    free_left = max(0, granted - used)

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
