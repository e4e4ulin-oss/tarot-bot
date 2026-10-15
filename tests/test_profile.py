"""Личный кабинет: разбор того, что человек пишет о себе."""

from datetime import date

import pytest

from bot.profile import describe, parse_city, parse_date, parse_time, skipped


@pytest.mark.parametrize(
    "text, expected",
    [
        ("21.05.1998", date(1998, 5, 21)),
        ("21.5.1998", date(1998, 5, 21)),
        ("1998-05-21", date(1998, 5, 21)),
        ("21/05/1998", date(1998, 5, 21)),
    ],
)
def test_birth_date_in_usual_spellings(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["вчера", "32.13.1998", "21.05.3000", "21.05.1800", ""])
def test_nonsense_is_not_a_date(text):
    assert parse_date(text) is None


@pytest.mark.parametrize("text, expected", [("7:05", "07:05"), ("07.05", "07:05"), ("23 59", "23:59")])
def test_birth_time_is_kept_as_written_on_the_clock(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["25:00", "вечером", "7"])
def test_impossible_time_is_refused(text):
    assert parse_time(text) is None


def test_city_is_trimmed_but_not_invented():
    assert parse_city("  Нижний   Новгород ") == "Нижний Новгород"
    assert parse_city("") is None
    assert parse_city("а" * 65) is None


def test_i_do_not_know_is_an_answer():
    assert skipped("не знаю") and skipped("Пропустить") and not skipped("Томск")


def test_description_skips_what_is_unknown():
    class U:
        birth_date = date(1998, 5, 21)
        birth_time = None
        birth_city = "Томск"
        city = None

    assert describe(U()) == "Дата рождения: 21.05.1998\nГород рождения: Томск"
    assert describe(None) == ""
