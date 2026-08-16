"""Раскладки: описание позиций и порядок карт."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Spread:
    key: str
    title: str
    subtitle: str
    positions: tuple[str, ...]
    yes_no: bool = False

    @property
    def size(self) -> int:
        return len(self.positions)


SPREADS: dict[str, Spread] = {
    "day": Spread(
        key="day",
        title="Карта дня",
        subtitle="Одна карта — настроение и задача дня",
        positions=("Карта дня",),
    ),
    "yes_no": Spread(
        key="yes_no",
        title="Да / Нет",
        subtitle="Короткий ответ на закрытый вопрос",
        positions=("Ответ",),
        yes_no=True,
    ),
    "ptf": Spread(
        key="ptf",
        title="Прошлое — Настоящее — Будущее",
        subtitle="Три карты: как сложилось, где вы сейчас, к чему идёт",
        positions=("Прошлое", "Настоящее", "Будущее"),
    ),
    "sit": Spread(
        key="sit",
        title="Ситуация — Совет — Итог",
        subtitle="Три карты: что происходит, что делать, чем закончится",
        positions=("Ситуация", "Совет", "Итог"),
    ),
    "love": Spread(
        key="love",
        title="Отношения",
        subtitle="Пять карт о паре: вы, партнёр и то, что между вами",
        positions=(
            "Вы в этих отношениях",
            "Партнёр в этих отношениях",
            "Что вас связывает",
            "Препятствие",
            "Перспектива",
        ),
    ),
    "celtic": Spread(
        key="celtic",
        title="Кельтский крест",
        subtitle="Десять карт — подробный разбор сложной ситуации",
        positions=(
            "Суть ситуации",
            "Что мешает или помогает",
            "Основание, корень",
            "Недавнее прошлое",
            "Цель, к чему вы стремитесь",
            "Ближайшее будущее",
            "Вы сами в ситуации",
            "Окружение и влияние людей",
            "Надежды и страхи",
            "Итог",
        ),
    ),
}

# Порядок в меню
SPREAD_ORDER: tuple[str, ...] = ("day", "yes_no", "ptf", "sit", "love", "celtic")


def get_spread(key: str) -> Spread:
    return SPREADS[key]
