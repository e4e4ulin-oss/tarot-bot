"""Астрономия: проверяется по событиям, которые известны независимо от нас."""

from datetime import datetime, timedelta

from bot.astro import SIGNS, ascendant, moon, sun


def test_sun_stands_at_zero_aries_at_the_vernal_equinox():
    """Весеннее равноденствие 2000 года — 20 марта, 07:35 UTC.

    Это и есть определение нуля Овна: в этот миг Солнце пересекает экватор.
    """
    position = sun(datetime(2000, 3, 20, 7, 35))
    assert min(position.degrees, 360 - position.degrees) < 0.05
    assert position.sign == "Овен"


def test_sun_at_the_start_of_2000_is_in_capricorn():
    position = sun(datetime(2000, 1, 1, 12, 0))
    assert 280.3 < position.degrees < 280.5
    assert position.sign == "Козерог"


def test_sun_walks_the_whole_circle_in_a_year():
    """За год Солнце возвращается почти туда же: год на сутки длиннее 364 дней."""
    start = sun(datetime(2026, 1, 1, 12, 0)).degrees
    later = sun(datetime(2026, 12, 31, 12, 0)).degrees
    gap = (later - start) % 360
    assert gap > 357, gap


def test_the_sun_warns_when_it_stands_on_a_border():
    """21 мая 1998 Солнце только входит в Близнецы: без времени — лотерея."""
    assert sun(datetime(1998, 5, 21, 7, 5)).near_cusp


def test_the_moon_changes_sign_within_three_days():
    first = moon(datetime(2026, 3, 1, 12, 0)).sign
    later = moon(datetime(2026, 3, 4, 12, 0)).sign
    assert first != later


def test_the_ascendant_goes_round_the_zodiac_in_a_day():
    start = datetime(2026, 3, 1, 0, 0)
    seen = {ascendant(start + timedelta(minutes=30 * step), 55.75, 37.62).sign for step in range(48)}
    assert seen == set(SIGNS)


def test_the_ascendant_depends_on_the_place():
    """Один и тот же миг, разные города — разный восходящий градус."""
    moment = datetime(1998, 5, 21, 7, 5)
    assert ascendant(moment, 56.5, 85.0).degrees != ascendant(moment, 55.75, 37.62).degrees


def test_degrees_inside_a_sign_are_cut_not_rounded():
    """Тридцатого градуса в знаке не бывает: 29.7° — всё ещё двадцать девятый."""
    from bot.astro import Position

    assert str(Position(59.7)) == "Телец 29°"
    assert str(Position(60.0)) == "Близнецы 0°"
