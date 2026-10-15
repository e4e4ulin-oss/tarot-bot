"""Что можно сказать о человеке по дате, времени и месту рождения.

Три величины требуют разного, и честнее сказать, чего не хватает, чем
посчитать наугад:

— Солнце считается от одной даты. Если дата пришлась на границу знаков,
  без времени и места знак не определить — так и пишем;
— Луна требует времени: за сутки она проходит тринадцать градусов, то есть
  почти ползнака;
— асцендент требует времени и города: он зависит от широты и сменяется
  примерно каждые два часа.

Местное время переводится в UTC по часовому поясу города. Правила поясов
берутся с историей: в 1998 году в России было летнее время, а у Москвы с
тех пор дважды менялся сдвиг — zoneinfo знает об этом, арифметика «плюс три»
не знает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from . import astro
from .geo import Place, find

# Без времени считаем на полдень: середина суток ошибается не больше чем на
# полсуток в любую сторону, и для Солнца этого хватает
NOON = time(12, 0)


@dataclass(frozen=True)
class Chart:
    sun: astro.Position
    moon: astro.Position | None = None
    ascendant: astro.Position | None = None
    place: Place | None = None
    exact_time: bool = False

    def lines(self) -> list[str]:
        out = [f"Солнце: {self.sun}"]
        if self.sun.near_cusp and not self.exact_time:
            out.append(
                "— Солнце стоит на границе знаков: без времени рождения "
                "знак может быть соседним"
            )
        if self.moon is not None:
            out.append(f"Луна: {self.moon}")
        if self.ascendant is not None:
            out.append(f"Асцендент: {self.ascendant}")
        elif self.exact_time and self.place is None:
            out.append("— асцендент не посчитан: город рождения не нашёлся на карте")
        return out


def _moment(born: date, clock: str | None, place: Place | None) -> tuple[datetime, bool]:
    """Местное время рождения в UTC. Второе значение — знаем ли мы время."""
    if clock:
        hour, minute = (int(part) for part in clock.split(":"))
        local = datetime.combine(born, time(hour, minute))
        exact = True
    else:
        local = datetime.combine(born, NOON)
        exact = False

    if place is None:
        # Пояса не знаем — считаем, что написанное время и есть UTC.
        # Для Солнца это ошибка в доли градуса, для Луны и асцендента
        # такой расчёт не делается вовсе
        return local, exact

    return local.replace(tzinfo=ZoneInfo(place.timezone)).astimezone(ZoneInfo("UTC")).replace(
        tzinfo=None
    ), exact


def chart(
    born: date,
    clock: str | None = None,
    city: str | None = None,
    place: Place | None = None,
) -> Chart:
    # Место может быть уже выбрано человеком из нескольких тёзок —
    # тогда искать по названию заново незачем
    place = place or (find(city) if city else None)
    moment, exact = _moment(born, clock, place)

    sun = astro.sun(moment)
    if not exact:
        # Луна за сутки уходит на ползнака, асцендент — на весь круг:
        # без времени это не расчёт, а выдумка
        return Chart(sun=sun, place=place, exact_time=False)

    moon = astro.moon(moment)
    if place is None:
        return Chart(sun=sun, moon=moon, exact_time=True)

    rising = astro.ascendant(moment, place.latitude, place.longitude)
    return Chart(sun=sun, moon=moon, ascendant=rising, place=place, exact_time=True)


@dataclass(frozen=True)
class Full:
    """Карта целиком: планеты, углы, дома и аспекты.

    Дома и углы появляются только когда известны и время, и место: без них
    это не карта, а список планет на день рождения.
    """

    planets: list[astro.Planet]
    aspects: list[astro.Aspect]
    ascendant: astro.Position | None = None
    midheaven: astro.Position | None = None
    place: Place | None = None
    exact_time: bool = False

    @property
    def has_houses(self) -> bool:
        return self.ascendant is not None

    def house_of(self, planet: astro.Planet) -> int | None:
        if self.ascendant is None:
            return None
        return astro.house_of(planet.position, self.ascendant)

    def lines(self, aspects_limit: int = 8) -> list[str]:
        out: list[str] = []
        if self.ascendant is not None and self.midheaven is not None:
            out.append(f"Асцендент: {self.ascendant}   Середина неба: {self.midheaven}")
            out.append("")

        for planet in self.planets:
            house = self.house_of(planet)
            tail = f"   {house} дом" if house else ""
            mark = " R" if planet.retrograde else ""
            out.append(f"{planet.name}: {planet.position}{mark}{tail}")

        if self.aspects:
            out.append("")
            out.append("Аспекты:")
            out.extend(f"{aspect}" for aspect in self.aspects[:aspects_limit])

        if not self.exact_time:
            out.append("")
            out.append(
                "Без времени рождения нет ни домов, ни асцендента, а Луна "
                "посчитана на полдень — за сутки она уходит на ползнака."
            )
        elif self.place is None:
            out.append("")
            out.append("Без города рождения нет домов и асцендента.")
        return out


def full(
    born: date,
    clock: str | None = None,
    city: str | None = None,
    place: Place | None = None,
) -> Full:
    place = place or (find(city) if city else None)
    moment, exact = _moment(born, clock, place)

    bodies = astro.planets(moment)
    links = astro.aspects(bodies)

    if not exact or place is None:
        return Full(planets=bodies, aspects=links, place=place, exact_time=exact)

    return Full(
        planets=bodies,
        aspects=links,
        ascendant=astro.ascendant(moment, place.latitude, place.longitude),
        midheaven=astro.midheaven(moment, place.longitude),
        place=place,
        exact_time=True,
    )


def points(card: Full) -> dict[str, astro.Position]:
    """Точки карты, к которым имеет смысл прикладывать транзиты."""
    out = {planet.name: planet.position for planet in card.planets}
    if card.ascendant is not None:
        out["Асцендент"] = card.ascendant
    if card.midheaven is not None:
        out["Середина неба"] = card.midheaven
    return out


def forecast(card: Full, moment: datetime | None = None) -> list[astro.Transit]:
    """Что сейчас стоит на градусах карты.

    Пустой список — нормальный ответ: месяцами может не быть ни одного
    близкого транзита, и придумывать их незачем.
    """
    return astro.transits(points(card), moment or datetime.utcnow())
