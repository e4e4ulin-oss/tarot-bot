"""Карта рождения и карта года.

Старый приём карточного чтения: цифры даты складываются в число старшего
аркана. Считается арифметикой, проверяется на бумаге и не зависит ни от
случайности, ни от модели — в отличие от расклада, это постоянная величина
человека.

Карта рождения — из полной даты. Карта года — из дня и месяца с текущим
годом: она меняется каждый год и говорит, под какой картой этот год идёт.
"""

from __future__ import annotations

from datetime import date

from .deck import Card, get_card

# Старших арканов 22: от Шута (0) до Мира (21)
MAJORS = 22


def _reduce(digits: int) -> int:
    """Сводит сумму к числу аркана.

    Двадцать два — это полный круг старших арканов, и по традиции он
    возвращает к Шуту, с которого всё начинается, а не к сумме своих цифр.
    """
    while digits > MAJORS:
        digits = sum(int(sign) for sign in str(digits))
    return 0 if digits == MAJORS else digits


def _digits(*numbers: int) -> int:
    return sum(int(sign) for number in numbers for sign in str(number))


def life_number(born: date) -> int:
    return _reduce(_digits(born.day, born.month, born.year))


def year_number(born: date, year: int) -> int:
    return _reduce(_digits(born.day, born.month, year))


def life_card(born: date) -> Card:
    return get_card(f"major_{life_number(born):02d}")


def year_card(born: date, year: int) -> Card:
    return get_card(f"major_{year_number(born, year):02d}")
