"""Вытягивание карт: честное перемешивание и сериализация расклада."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from random import Random

from .deck import Card, card_title, get_card, load_deck
from .spreads import Spread


@dataclass(frozen=True, slots=True)
class DrawnCard:
    position: str
    card: Card
    reversed: bool

    @property
    def title(self) -> str:
        suffix = " (перевёрнутая)" if self.reversed else ""
        return f"{card_title(self.card)}{suffix}"

    @property
    def meaning(self) -> str:
        return self.card.meaning(self.reversed)

    @property
    def keywords(self) -> tuple[str, ...]:
        return self.card.keywords(self.reversed)


def draw_spread(
    spread: Spread,
    *,
    allow_reversed: bool = True,
    reversed_chance: float = 0.4,
    rng: Random | None = None,
) -> list[DrawnCard]:
    """Вытягивает карты без повторов.

    По умолчанию источник случайности — криптографический (secrets).
    """
    rng = rng or Random(secrets.randbits(128))
    cards = rng.sample(load_deck(), spread.size)
    return [
        DrawnCard(
            position=position,
            card=card,
            reversed=allow_reversed and rng.random() < reversed_chance,
        )
        for position, card in zip(spread.positions, cards, strict=True)
    ]


def serialize(drawn: list[DrawnCard]) -> list[dict]:
    return [{"position": d.position, "card": d.card.id, "reversed": d.reversed} for d in drawn]


def deserialize(data: list[dict]) -> list[DrawnCard]:
    return [
        DrawnCard(
            position=item["position"],
            card=get_card(item["card"]),
            reversed=bool(item["reversed"]),
        )
        for item in data
    ]


# --- Логика расклада «Да / Нет» -------------------------------------------------
# 1 — карта отвечает «да», -1 — «нет», 0 — ответ неоднозначный.
# Перевёрнутое положение переворачивает полярность.

_MAJOR_POLARITY: dict[int, int] = {
    0: 0,
    1: 1,
    2: 0,
    3: 1,
    4: 1,
    5: 0,
    6: 1,
    7: 1,
    8: 1,
    9: 0,
    10: 1,
    11: 0,
    12: -1,
    13: -1,
    14: 1,
    15: -1,
    16: -1,
    17: 1,
    18: -1,
    19: 1,
    20: 1,
    21: 1,
}

_MINOR_POLARITY: dict[str, dict[int, int]] = {
    "wands": {1: 1, 2: 1, 3: 1, 4: 1, 5: -1, 6: 1, 7: -1, 8: 1, 9: -1, 10: -1},
    "cups": {1: 1, 2: 1, 3: 1, 4: -1, 5: -1, 6: 1, 7: -1, 8: -1, 9: 1, 10: 1},
    "swords": {1: 1, 2: 0, 3: -1, 4: 0, 5: -1, 6: 1, 7: -1, 8: -1, 9: -1, 10: -1},
    "pentacles": {1: 1, 2: 0, 3: 1, 4: 1, 5: -1, 6: 1, 7: 0, 8: 1, 9: 1, 10: 1},
}


def card_polarity(card: Card) -> int:
    if card.is_major:
        return _MAJOR_POLARITY.get(card.number, 0)
    return _MINOR_POLARITY.get(card.suit or "", {}).get(card.number, 0)


def yes_no_verdict(drawn: DrawnCard) -> str:
    """Короткий вердикт для расклада «Да / Нет»."""
    polarity = card_polarity(drawn.card)
    if drawn.reversed:
        polarity = -polarity
    if polarity > 0:
        return "Скорее да"
    if polarity < 0:
        return "Скорее нет"
    return "Ответ пока не определён"
