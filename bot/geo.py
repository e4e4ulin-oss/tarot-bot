"""Город рождения → координаты и часовой пояс.

Нужно ради асцендента: он зависит от широты и от местного времени, а местное
время — от пояса. База городов лежит в пакете geonamescache (данные GeoNames,
CC-BY): офлайн, без ключей и без запросов наружу.

Берутся города от пятнадцати тысяч жителей. Деревня рождения в базе не
найдётся — тогда человек пишет ближайший город, и это честнее, чем молча
промахнуться на сотню километров.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import geonamescache


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    timezone: str


def _normalize(name: str) -> str:
    return " ".join(name.lower().replace("ё", "е").split())


@lru_cache(maxsize=1)
def _index() -> dict[str, Place]:
    """Имя во всех написаниях → город. Крупный город вытесняет тёзку-посёлок."""
    index: dict[str, Place] = {}
    population: dict[str, int] = {}

    for city in geonamescache.GeonamesCache().get_cities().values():
        place = Place(
            name=city["name"],
            latitude=float(city["latitude"]),
            longitude=float(city["longitude"]),
            timezone=city["timezone"],
        )
        names = [city["name"], *city.get("alternatenames", [])]
        for name in names:
            key = _normalize(name)
            if not key:
                continue
            if population.get(key, -1) >= city["population"]:
                continue
            index[key] = place
            population[key] = city["population"]
    return index


def find(name: str) -> Place | None:
    return _index().get(_normalize(name))
