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
from .utils import fmt_day

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
    moment: datetime | None = None  # нужен узлам и фазе Луны

    @property
    def has_houses(self) -> bool:
        return self.ascendant is not None

    def house_of(self, planet: astro.Planet) -> int | None:
        if self.ascendant is None:
            return None
        return astro.house_of(planet.position, self.ascendant)

    @property
    def node(self) -> astro.Position | None:
        """Северный узел Луны: сторона, в которую карта растёт."""
        return astro.north_node(self.moment) if self.moment is not None else None

    @property
    def phase(self) -> str | None:
        return astro.moon_phase(self.moment) if self.moment is not None else None

    def rulers(self) -> list[astro.Ruler]:
        if self.ascendant is None:
            return []
        return astro.rulers(self.ascendant, self.planets)

    def nodes_line(self) -> str:
        """Ось узлов одной строкой: южный узел — это северный напротив."""
        north = self.node
        assert north is not None
        south = astro.Position((north.degrees + 180) % 360)
        line = f"Узлы: северный {north} — южный {south}"
        if self.ascendant is not None:
            line += (
                f" ({astro.house_of(north, self.ascendant)}"
                f" и {astro.house_of(south, self.ascendant)} дома)"
            )
        return line

    def lines(self, aspects_limit: int = 8, deep: bool = False) -> list[str]:
        out: list[str] = []
        if self.ascendant is not None and self.midheaven is not None:
            out.append(f"Асцендент: {self.ascendant}   Середина неба: {self.midheaven}")
            out.append("")

        for planet in self.planets:
            house = self.house_of(planet)
            tail = f"   {house} дом" if house else ""
            mark = " R" if planet.retrograde else ""
            out.append(f"{planet.name}: {planet.position}{mark}{tail}")

        out.append("")
        elements, modes = astro.balance(self.planets)
        out.append("Стихии: " + ", ".join(f"{name} {count}" for name, count in elements.items()))
        out.append("Кресты: " + ", ".join(f"{name} {count}" for name, count in modes.items()))
        if self.phase is not None:
            out.append(f"Фаза Луны при рождении: {self.phase}")
        if self.node is not None:
            out.append(self.nodes_line())

        if self.aspects:
            out.append("")
            out.append("Аспекты:")
            out.extend(f"{aspect}" for aspect in self.aspects[:aspects_limit])

        if deep and self.ascendant is not None:
            out.append("")
            out.append("Управители домов:")
            out.extend(f"{ruler}" for ruler in self.rulers())

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
        return Full(
            planets=bodies, aspects=links, place=place, exact_time=exact, moment=moment
        )

    rising = astro.ascendant(moment, place.latitude, place.longitude)
    upper = astro.midheaven(moment, place.longitude)

    # Аспекты к углам идут в общий список и сортируются вместе с остальными:
    # точное соединение с асцендентом важнее широкого тригона между планетами
    links = sorted(
        links + astro.angle_aspects(bodies, rising, upper),
        key=lambda aspect: aspect.exact,
    )

    return Full(
        planets=bodies,
        aspects=links,
        ascendant=rising,
        midheaven=upper,
        place=place,
        exact_time=True,
        moment=moment,
    )


def points(card: Full) -> dict[str, astro.Position]:
    """Точки карты, к которым имеет смысл прикладывать транзиты."""
    out = {planet.name: planet.position for planet in card.planets}
    if card.ascendant is not None:
        out["Асцендент"] = card.ascendant
    if card.midheaven is not None:
        out["Середина неба"] = card.midheaven
    return out


def when(transit: astro.Transit, card: Full, moment: datetime | None = None) -> astro.Timing:
    """Когда этот транзит был и будет точен и до каких пор он в силе."""
    natal = points(card)[transit.natal]
    angle = next(angle for kind, angle, _ in astro.ASPECTS if kind == transit.kind)
    return astro.timing(transit.moving, natal.degrees, angle, moment or datetime.utcnow())


def timeline(transit: astro.Transit, card: Full, moment: datetime | None = None) -> str:
    """Срок транзита словами: когда точен и до каких пор в силе.

    Планета может подойти к градусу и отступить, так и не став точной, —
    тогда так и говорится. Выдавать прошлые точные даты за будущие нельзя:
    человек прочтёт их как обещание.
    """
    moment = moment or datetime.utcnow()
    marks = when(transit, card, moment)
    ahead = [day for day in marks.exact if day.date() >= moment.date()]

    if ahead:
        dates = ", ".join(fmt_day(day, moment) for day in ahead[:3])
        line = f"точный {dates}"
    else:
        line = "точным в этот заход не станет — подходит и отступает"

    if marks.leaves is not None:
        line += f"; в силе до {fmt_day(marks.leaves, moment)}"
    return line


def forecast(card: Full, moment: datetime | None = None) -> list[astro.Transit]:
    """Что сейчас стоит на градусах карты.

    Пустой список — нормальный ответ: месяцами может не быть ни одного
    близкого транзита, и придумывать их незачем.
    """
    return astro.transits(points(card), moment or datetime.utcnow())
