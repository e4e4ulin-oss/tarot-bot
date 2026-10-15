"""Что считается по дате, времени и месту — и чего не считается без них."""

from datetime import date

import pytest

from bot.natal import chart


def test_only_a_date_gives_only_the_sun():
    """Луна за сутки уходит на ползнака, асцендент — на весь круг."""
    result = chart(date(1998, 5, 21))
    assert result.sun is not None
    assert result.moon is None and result.ascendant is None


def test_a_date_on_the_border_says_so_out_loud():
    lines = "\n".join(chart(date(1998, 5, 21)).lines())
    assert "границе знаков" in lines


def test_time_without_a_city_still_gives_the_moon():
    result = chart(date(1998, 5, 21), "07:05")
    assert result.moon is not None
    assert result.ascendant is None


def test_the_ascendant_needs_both_time_and_city():
    result = chart(date(1998, 5, 21), "07:05", "Томск")
    assert result.ascendant is not None
    assert result.place.name == "Tomsk"


def test_the_same_birth_minute_in_two_cities_differs():
    """Разные пояса и широты — разное небо над головой."""
    east = chart(date(1998, 5, 21), "07:05", "Томск")
    west = chart(date(1998, 5, 21), "07:05", "Москва")
    assert east.ascendant.degrees != west.ascendant.degrees


def test_an_unknown_city_does_not_break_the_chart():
    result = chart(date(1998, 5, 21), "07:05", "Выдуманогорск")
    assert result.moon is not None and result.ascendant is None


@pytest.mark.parametrize("city", ["Томск", "томск", " Томск "])
def test_city_is_recognised_however_it_is_written(city):
    assert chart(date(1998, 5, 21), "07:05", city).place is not None
