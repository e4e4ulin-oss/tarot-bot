"""Колода Таро: загрузка карт из data/cards.json и модель карты."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "cards.json"

MAJOR_ROMAN = [
    "0",
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
    "XIII",
    "XIV",
    "XV",
    "XVI",
    "XVII",
    "XVIII",
    "XIX",
    "XX",
    "XXI",
]

SUIT_EMOJI = {
    "wands": "🔥",
    "cups": "💧",
    "swords": "🌬",
    "pentacles": "🌿",
    "major": "✨",
}


@dataclass(frozen=True, slots=True)
class Card:
    """Одна карта колоды со значениями в прямом и перевёрнутом положении."""

    id: str
    name: str
    arcana: str  # "major" | "minor"
    suit: str | None  # None для старших арканов
    number: int
    correspondence: str  # планета/знак для старших, стихия для младших
    keywords_upright: tuple[str, ...]
    keywords_reversed: tuple[str, ...]
    meaning_upright: str
    meaning_reversed: str
    advice: str = ""
    name_en: str = ""

    @property
    def emoji(self) -> str:
        return SUIT_EMOJI[self.suit or "major"]

    @property
    def is_major(self) -> bool:
        return self.arcana == "major"

    def keywords(self, reversed_: bool) -> tuple[str, ...]:
        return self.keywords_reversed if reversed_ else self.keywords_upright

    def meaning(self, reversed_: bool) -> str:
        return self.meaning_reversed if reversed_ else self.meaning_upright


def _load_raw() -> dict:
    with DATA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_deck() -> tuple[Card, ...]:
    """Собирает полную колоду из 78 карт. Результат кэшируется на процесс."""
    raw = _load_raw()
    ranks: dict[str, str] = raw["ranks"]
    suits: dict[str, dict] = raw["suits"]
    cards: list[Card] = []

    for item in raw["majors"]:
        number = int(item["number"])
        cards.append(
            Card(
                id=f"major_{number:02d}",
                name=item["name"],
                arcana="major",
                suit=None,
                number=number,
                correspondence=item.get("corr", ""),
                keywords_upright=tuple(item["ku"]),
                keywords_reversed=tuple(item["kr"]),
                meaning_upright=item["up"],
                meaning_reversed=item["rev"],
                advice=item.get("advice", ""),
                name_en=item.get("en", ""),
            )
        )

    for suit_key, items in raw["minors"].items():
        suit = suits[suit_key]
        for item in items:
            rank = int(item["rank"])
            cards.append(
                Card(
                    id=f"{suit_key}_{rank:02d}",
                    name=f"{ranks[str(rank)]} {suit['genitive']}",
                    arcana="minor",
                    suit=suit_key,
                    number=rank,
                    correspondence=suit["element"],
                    keywords_upright=tuple(item["ku"]),
                    keywords_reversed=tuple(item["kr"]),
                    meaning_upright=item["up"],
                    meaning_reversed=item["rev"],
                )
            )

    return tuple(cards)


@lru_cache(maxsize=1)
def deck_index() -> dict[str, Card]:
    return {card.id: card for card in load_deck()}


def get_card(card_id: str) -> Card:
    return deck_index()[card_id]


@lru_cache(maxsize=1)
def suit_info() -> dict[str, dict]:
    return _load_raw()["suits"]


def card_title(card: Card) -> str:
    """Читаемое имя карты: «✨ XIII. Смерть» или «💧 Туз Кубков»."""
    if card.is_major:
        return f"{card.emoji} {MAJOR_ROMAN[card.number]}. {card.name}"
    return f"{card.emoji} {card.name}"
