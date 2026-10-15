"""Карта рождения и карта года: арифметика, которую можно проверить на бумаге."""

from datetime import date

import pytest

from bot.tarot import life_card, life_number, year_card, year_number
from bot.tarot.birth import MAJORS, _reduce


def test_life_number_is_the_sum_of_the_digits():
    # 2+1+0+5+1+9+9+8 = 35 → 3+5 = 8
    assert life_number(date(1998, 5, 21)) == 8
    assert life_card(date(1998, 5, 21)).name == "Сила"


def test_year_number_takes_the_day_and_month_with_this_year():
    # 2+1+0+5+2+0+2+6 = 18
    assert year_number(date(1998, 5, 21), 2026) == 18
    assert year_card(date(1998, 5, 21), 2026).name == "Луна"


def test_the_year_card_changes_every_year():
    born = date(1998, 5, 21)
    assert year_number(born, 2026) != year_number(born, 2027)


def test_full_circle_returns_to_the_fool():
    """Двадцать два — круг старших арканов: он возвращает к началу, а не к 4."""
    assert _reduce(MAJORS) == 0


@pytest.mark.parametrize("day", [date(1900, 1, 1), date(2024, 12, 31), date(1987, 7, 7)])
def test_any_date_lands_inside_the_major_arcana(day):
    assert 0 <= life_number(day) <= 21
    assert 0 <= year_number(day, 2026) <= 21


def test_people_born_the_same_day_share_the_card():
    """Это не расклад: карта рождения — общая у всех, кто родился в тот день."""
    assert life_number(date(1998, 5, 21)) == life_number(date(1998, 5, 21))
