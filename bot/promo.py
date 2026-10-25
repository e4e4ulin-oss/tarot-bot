"""Промокоды: несколько бесплатных раскладов по слову.

Правила отделены от чата нарочно: промокод скоро будут вводить и в
мини-приложении, а два разных ответа на «действует ли код» — это или обиженный
человек, или бесконечный код.

Проверки идут по одной и называются словами, потому что «код не подошёл» —
худший из возможных ответов: человек не понимает, опечатался он, опоздал или
уже вводил.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from . import analytics
from .db import Repo
from .db.models import PromoCode

# Коды набирают как придётся: с пробелами, в другом регистре, с дефисами
TRIM = " \t\n-–—"


def normalize(code: str) -> str:
    return code.strip(TRIM).upper().replace(" ", "")


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str  # applied | unknown | disabled | expired | spent | already | empty
    readings: int = 0
    total: int = 0  # остаток бонусных раскладов после начисления


def today() -> object:
    return datetime.now(UTC).date()


def check(promo: PromoCode | None) -> str | None:
    """Почему код не сработает, или None, если сработает."""
    if promo is None:
        return "unknown"
    if not promo.active:
        return "disabled"
    if promo.expires_at is not None and promo.expires_at < today():
        return "expired"
    if promo.uses_limit and promo.used >= promo.uses_limit:
        return "spent"
    return None


async def redeem(repo: Repo, user_id: int, code: str) -> Result:
    """Пробует применить код к человеку."""
    code = normalize(code)
    if not code:
        return Result(False, "empty")

    promo = await repo.promo_by_code(code)
    if (refusal := check(promo)) is not None:
        return Result(False, refusal)
    assert promo is not None

    if await repo.promo_used_by(promo.id, user_id):
        return Result(False, "already")

    await repo.take_promo(promo, user_id)
    user = await repo.get_user(user_id)
    await repo.log_event(user_id, analytics.PROMO_USED, promo.code)
    return Result(
        True,
        "applied",
        readings=promo.readings,
        total=user.bonus_readings if user else promo.readings,
    )
