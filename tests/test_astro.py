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


def test_the_great_conjunction_of_2020_is_in_place():
    """21 декабря 2020 Юпитер и Сатурн сошлись в начале Водолея.

    Событие известное и проверяемое по любому источнику: если расчёт врёт,
    он соврёт и здесь.
    """
    from bot.astro import planets

    bodies = {planet.name: planet for planet in planets(datetime(2020, 12, 21, 18, 20))}
    jupiter = bodies["Юпитер"].position
    saturn = bodies["Сатурн"].position

    assert abs(jupiter.degrees - saturn.degrees) < 0.5
    assert jupiter.sign == "Водолей" and saturn.sign == "Водолей"
    assert jupiter.degrees_in_sign < 1


def test_mercury_never_runs_far_from_the_sun():
    """Меркурий не отходит от Солнца больше чем на 28° — это астрономия."""
    from bot.astro import planets

    for day in (1, 90, 180, 270):
        bodies = {p.name: p.position.degrees for p in planets(datetime(2026, 1, 1) + timedelta(days=day))}
        gap = abs(bodies["Солнце"] - bodies["Меркурий"]) % 360
        assert min(gap, 360 - gap) <= 28.5


def test_venus_stays_within_its_elongation_too():
    from bot.astro import planets

    for day in (0, 120, 240):
        bodies = {p.name: p.position.degrees for p in planets(datetime(2026, 1, 1) + timedelta(days=day))}
        gap = abs(bodies["Солнце"] - bodies["Венера"]) % 360
        assert min(gap, 360 - gap) <= 48.5


def test_outer_planets_are_retrograde_about_five_months_a_year():
    """Уран идёт попятно примерно 150 дней в году: это следствие орбит."""
    from bot.astro import planets

    days = [
        any(p.name == "Уран" and p.retrograde for p in planets(datetime(2026, 1, 1) + timedelta(days=n * 10)))
        for n in range(36)
    ]
    assert 10 <= sum(days) <= 20


def test_houses_start_at_the_ascendant():
    from bot.astro import Position, house_of

    asc = Position(100.0)
    assert house_of(Position(100.0), asc) == 1
    assert house_of(Position(131.0), asc) == 2
    assert house_of(Position(99.0), asc) == 12


def test_an_aspect_is_found_only_within_its_orb():
    from bot.astro import Planet, Position, aspects

    close = aspects([Planet("Марс", Position(10)), Planet("Сатурн", Position(14))])
    far = aspects([Planet("Марс", Position(10)), Planet("Сатурн", Position(30))])
    assert close and close[0].kind == "соединение"
    assert not far
