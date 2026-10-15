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
    population: int = 0
    country: str = ""
    geonameid: int = 0

    @property
    def hint(self) -> str:
        """Чем один тёзка отличается от другого в глазах человека."""
        size = f"{self.population // 1000} тыс." if self.population >= 1000 else "посёлок"
        return f"{size} · {self.timezone}"


def _normalize(name: str) -> str:
    return " ".join(name.lower().replace("ё", "е").split())


@lru_cache(maxsize=1)
def _index() -> dict[str, list[Place]]:
    """Имя во всех написаниях → все тёзки, от крупного к мелкому.

    Раньше крупный город просто вытеснял тёзку, и человек, родившийся в
    Ростове Великом, получал небо Ростова-на-Дону. Теперь выбор за ним.
    """
    index: dict[str, list[Place]] = {}

    for city in geonamescache.GeonamesCache().get_cities().values():
        place = Place(
            name=city["name"],
            latitude=float(city["latitude"]),
            longitude=float(city["longitude"]),
            timezone=city["timezone"],
            population=int(city["population"]),
            country=city["countrycode"],
            geonameid=int(city["geonameid"]),
        )
        for name in [city["name"], *city.get("alternatenames", [])]:
            key = _normalize(name)
            if key:
                index.setdefault(key, []).append(place)

    for places in index.values():
        places.sort(key=lambda place: -place.population)
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


def find_all(name: str) -> list[Place]:
    """Все города с таким именем — от крупного к мелкому."""
    index = _index()
    for guess in _candidates(name):
        places = index.get(_normalize(guess))
        if places:
            return places
    return []


def find(name: str) -> Place | None:
    """Самый крупный тёзка. Годится, когда выбирать не из чего или некогда."""
    places = find_all(name)
    return places[0] if places else None


@lru_cache(maxsize=1)
def _by_id() -> dict[int, Place]:
    return {place.geonameid: place for places in _index().values() for place in places}


def by_id(geonameid: int) -> Place | None:
    """Место, выбранное человеком из нескольких тёзок."""
    return _by_id().get(geonameid)
