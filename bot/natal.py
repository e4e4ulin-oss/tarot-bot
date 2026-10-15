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


def chart(born: date, clock: str | None = None, city: str | None = None) -> Chart:
    place = find(city) if city else None
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
