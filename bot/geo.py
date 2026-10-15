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


# Люди пишут адрес, а не название: «г. Энгельс, Саратовской обл».
# Отбрасываем то, что городом не является.
PREFIXES = ("г.", "г", "гор.", "город", "пос.", "посёлок", "поселок", "с.", "село", "ст.")
TAILS = ("область", "обл.", "обл", "район", "р-н", "край", "республика", "россия", "рф")


def _candidates(name: str) -> list[str]:
    """Что пробовать искать: от написанного целиком до одного слова."""
    cleaned = name.replace("ё", "е")
    seen: list[str] = []

    for part in [cleaned, *cleaned.split(",")]:
        words = part.strip().split()
        while words and words[0].lower().strip(".") in {p.strip(".") for p in PREFIXES}:
            words = words[1:]
        while words and words[-1].lower().strip(".") in {t.strip(".") for t in TAILS}:
            words = words[:-1]
        if words:
            guess = " ".join(words)
            if guess not in seen:
                seen.append(guess)
    return seen


def find(name: str) -> Place | None:
    index = _index()
    for guess in _candidates(name):
        place = index.get(_normalize(guess))
        if place is not None:
            return place
    return None
