"""Уровни авторского разбора: что входит и сколько стоит.

Цены задаются в `.env`, чтобы менять их без правки кода. Уровень с нулевой ценой
не показывается вовсе — так можно временно оставить, например, только лаконичный.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class Tier:
    key: str
    title: str
    price: int
    # Одной строкой — для кнопки и сводки заявки
    summary: str
    # Построчно — для списка уровней перед выбором
    includes: tuple[str, ...]

    def price_label(self, currency: str = "₽") -> str:
        return f"{self.price} {currency}"

    def button_label(self, currency: str = "₽") -> str:
        return f"{self.title} · {self.price_label(currency)}"


# Тексты описывают объём работы, а не сроки: обещать срок бот не вправе.
_TIERS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "short",
        "Лаконичный",
        "три карты, короткий ответ по сути",
        (
            "расклад из трёх карт на ваш вопрос",
            "короткий ответ: что происходит и что с этим делать",
            "без разбора каждой карты по отдельности",
        ),
    ),
    (
        "base",
        "Базовый",
        "расклад под вопрос и развёрнутый разбор",
        (
            "расклад подбирается под вашу ситуацию",
            "разбор каждой карты и общий вывод",
            "один уточняющий вопрос после ответа",
        ),
    ),
    (
        "full",
        "Полный",
        "большой расклад, разбор по блокам, рекомендации",
        (
            "развёрнутый расклад с несколькими позициями",
            "разбор по блокам: ситуация, что скрыто, препятствия, совет, к чему идёт",
            "практические рекомендации",
            "уточняющие вопросы после ответа",
        ),
    ),
)


def _price(settings: Settings, key: str) -> int:
    return {
        "short": settings.author_price_short,
        "base": settings.author_price_base,
        "full": settings.author_price_full,
    }[key]


def tiers(settings: Settings) -> tuple[Tier, ...]:
    """Уровни, доступные клиенту: нулевая цена означает «не предлагаем»."""
    return tuple(
        Tier(key=key, title=title, price=_price(settings, key), summary=summary, includes=includes)
        for key, title, summary, includes in _TIERS
        if _price(settings, key) > 0
    )


def find_tier(settings: Settings, key: str | None) -> Tier | None:
    if not key:
        return None
    return next((tier for tier in tiers(settings) if tier.key == key), None)


def tier_label(settings: Settings, key: str | None) -> str:
    """Название с ценой. Заявки, созданные до появления уровней, остаются без него."""
    tier = find_tier(settings, key)
    if tier is None:
        return "не выбран"
    return f"{tier.title} · {tier.price_label(settings.author_currency)}"
