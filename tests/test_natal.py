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


@pytest.mark.parametrize(
    "written",
    [
        "Энгельс",
        "г. Энгельс, Саратовской обл",
        "Энгельс, Саратовская область",
        "г Энгельс",
        "ЭНГЕЛЬС",
    ],
)
def test_people_write_an_address_not_a_name(written):
    """«г. Энгельс, Саратовской обл» — это тоже Энгельс."""
    assert chart(date(1998, 5, 21), "07:05", written).place.name == "Engels"


def test_an_unknown_place_says_why_there_is_no_ascendant():
    lines = "\n".join(chart(date(1998, 5, 21), "07:05", "пос. Токсово").lines())
    assert "не нашёлся на карте" in lines


def test_the_full_chart_shows_the_elements_the_phase_and_the_nodes():
    from bot.natal import full

    lines = "\n".join(full(date(1987, 1, 1), "03:40", "Москва").lines())
    assert "Стихии:" in lines and "Кресты:" in lines
    assert "Фаза Луны при рождении:" in lines
    assert "Узлы: северный" in lines and "южный" in lines


def test_the_nodes_are_half_a_circle_apart_and_in_opposite_houses():
    """Южный узел — это северный напротив: одна ось, два конца."""
    from bot.natal import full

    card = full(date(1987, 1, 1), "03:40", "Москва")
    line = card.nodes_line()

    houses = line.rsplit("(", 1)[1].replace("дома)", "")
    north, south = (int(part) for part in houses.split(" и "))
    assert (south - north) % 12 == 6


def test_the_house_rulers_appear_only_in_the_deep_reading():
    """Управители — для платного разбора: в бесплатной карте это шум."""
    from bot.natal import full

    card = full(date(1987, 1, 1), "03:40", "Москва")
    assert "Управители домов:" not in "\n".join(card.lines())

    deep = "\n".join(card.lines(deep=True))
    assert "Управители домов:" in deep
    assert len([line for line in deep.splitlines() if " дом (" in line]) == 12


def test_the_angles_are_aspected_along_with_the_planets():
    from bot.natal import full

    card = full(date(1987, 1, 1), "03:40", "Москва")
    named = {aspect.second for aspect in card.aspects}
    assert "Асцендент" in named or "Середина неба" in named

    # Список остаётся отсортированным по точности после слияния
    exact = [aspect.exact for aspect in card.aspects]
    assert exact == sorted(exact)


def test_a_chart_without_a_city_has_no_rulers_but_keeps_the_nodes():
    from bot.natal import full

    card = full(date(1987, 1, 1), "03:40")
    assert card.rulers() == []
    assert card.node is not None
    assert "Управители домов:" not in "\n".join(card.lines(deep=True))
