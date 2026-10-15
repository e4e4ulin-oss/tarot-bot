"""Солнце, Луна и асцендент на момент рождения.

Считается по эфемеридам (библиотека ephem), а не по приметам: это обычная
астрономия. Астрологическое здесь только деление круга на двенадцать частей
и названия.

Три величины требуют разного:
— знак Солнца виден почти всегда: Солнце идёт по градусу в сутки, и хватает
  одной даты. На границе знака сутки решают, и об этом нужно предупреждать;
— знак Луны требует времени: Луна проходит знак за двое с небольшим суток;
— асцендент требует и времени, и места: он зависит от широты и меняется
  полностью за сутки, по знаку каждые два часа.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import ephem

SIGNS: tuple[str, ...] = (
    "Овен",
    "Телец",
    "Близнецы",
    "Рак",
    "Лев",
    "Дева",
    "Весы",
    "Скорпион",
    "Стрелец",
    "Козерог",
    "Водолей",
    "Рыбы",
)

SIGN = 30.0
# Ближе этого к границе знака сутки без времени рождения уже решают дело
CUSP = 1.0


@dataclass(frozen=True)
class Position:
    degrees: float  # долгота по эклиптике, 0–360

    @property
    def sign(self) -> str:
        return SIGNS[int(self.degrees // SIGN) % 12]

    @property
    def degrees_in_sign(self) -> float:
        return self.degrees % SIGN

    @property
    def near_cusp(self) -> bool:
        edge = self.degrees % SIGN
        return edge < CUSP or edge > SIGN - CUSP

    def __str__(self) -> str:
        # Градусы внутри знака отсекаются, а не округляются: 29.7° — это ещё
        # двадцать девятый градус Тельца, а не тридцатый, которого не бывает
        return f"{self.sign} {int(self.degrees_in_sign)}°"


def _longitude(body: ephem.Body) -> Position:
    return Position(math.degrees(ephem.Ecliptic(body).lon) % 360)


def sun(moment: datetime) -> Position:
    return _longitude(ephem.Sun(ephem.Date(moment)))


def moon(moment: datetime) -> Position:
    return _longitude(ephem.Moon(ephem.Date(moment)))


def ascendant(moment: datetime, latitude: float, longitude: float) -> Position:
    """Точка эклиптики, восходящая на востоке в этот миг в этом месте.

    Через звёздное время места: оно даёт градус небесного меридиана, а
    наклон эклиптики и широта — поворот к точке восхода.
    """
    observer = ephem.Observer()
    observer.lat, observer.lon = str(latitude), str(longitude)
    observer.date = ephem.Date(moment)

    ramc = float(observer.sidereal_time())  # радианы
    tilt = math.radians(23.4392911)  # наклон эклиптики к экватору
    phi = math.radians(latitude)

    value = math.atan2(
        math.cos(ramc),
        -(math.sin(ramc) * math.cos(tilt) + math.tan(phi) * math.sin(tilt)),
    )
    return Position(math.degrees(value) % 360)


# Светила и планеты в том порядке, в каком их читают в карте
PLANETS: tuple[tuple[str, str], ...] = (
    ("Sun", "Солнце"),
    ("Moon", "Луна"),
    ("Mercury", "Меркурий"),
    ("Venus", "Венера"),
    ("Mars", "Марс"),
    ("Jupiter", "Юпитер"),
    ("Saturn", "Сатурн"),
    ("Uranus", "Уран"),
    ("Neptune", "Нептун"),
    ("Pluto", "Плутон"),
)

# Угол между планетами, который считается значимым, и допуск к нему.
# Светилам допуск шире: они главные, и их связи читают первыми.
ASPECTS: tuple[tuple[str, float, float], ...] = (
    ("соединение", 0.0, 8.0),
    ("секстиль", 60.0, 4.0),
    ("квадрат", 90.0, 6.0),
    ("тригон", 120.0, 6.0),
    ("оппозиция", 180.0, 8.0),
)

LUMINARIES = ("Солнце", "Луна")
WIDER = 2.0  # насколько шире допуск, если участвует светило


@dataclass(frozen=True)
class Planet:
    name: str
    position: Position
    retrograde: bool = False

    def __str__(self) -> str:
        return f"{self.name}: {self.position}" + (" (R)" if self.retrograde else "")


@dataclass(frozen=True)
class Aspect:
    first: str
    second: str
    kind: str
    exact: float  # насколько угол отличается от точного, в градусах

    def __str__(self) -> str:
        return f"{self.first} — {self.second}: {self.kind} ({self.exact:.1f}°)"


def planets(moment: datetime) -> list[Planet]:
    """Положения планет и то, идут ли они попятно.

    Попятность определяется сравнением с положением сутками раньше: это и
    есть ретроградность — видимое движение назад по зодиаку.
    """
    out = []
    yesterday = ephem.Date(moment) - 1
    for code, name in PLANETS:
        body = getattr(ephem, code)
        now = Position(math.degrees(ephem.Ecliptic(body(ephem.Date(moment))).lon) % 360)
        before = Position(math.degrees(ephem.Ecliptic(body(yesterday)).lon) % 360)
        step = (now.degrees - before.degrees + 540) % 360 - 180
        out.append(Planet(name=name, position=now, retrograde=step < 0))
    return out


def midheaven(moment: datetime, longitude: float) -> Position:
    """Середина неба: градус эклиптики на небесном меридиане."""
    observer = ephem.Observer()
    observer.lat, observer.lon = "0", str(longitude)
    observer.date = ephem.Date(moment)
    ramc = float(observer.sidereal_time())
    tilt = math.radians(23.4392911)
    return Position(math.degrees(math.atan2(math.sin(ramc), math.cos(ramc) * math.cos(tilt))) % 360)


def house_of(position: Position, ascendant: Position) -> int:
    """Дом по равнодомной системе: первый начинается с асцендента.

    Система выбрана не случайно: равные дома считаются однозначно и
    проверяются арифметикой. Плацидус популярнее, но у него другие границы,
    и подавать его как «тот самый» без сверки с эфемеридами нечестно.
    """
    return int(((position.degrees - ascendant.degrees) % 360) // SIGN) + 1


def aspects(bodies: list[Planet]) -> list[Aspect]:
    """Значимые углы между планетами, от точных к широким."""
    found = []
    for index, first in enumerate(bodies):
        for second in bodies[index + 1 :]:
            gap = abs(first.position.degrees - second.position.degrees) % 360
            gap = min(gap, 360 - gap)
            for kind, angle, orb in ASPECTS:
                allowed = orb + (
                    WIDER if first.name in LUMINARIES or second.name in LUMINARIES else 0
                )
                if abs(gap - angle) <= allowed:
                    found.append(
                        Aspect(first.name, second.name, kind, abs(gap - angle))
                    )
                    break
    return sorted(found, key=lambda aspect: aspect.exact)


# Кого слушают в прогнозе: быстрые планеты задевают всех каждый месяц, и
# говорить о них нечего. Смысл несут медленные — они стоят на градусе месяцами.
SLOW = ("Юпитер", "Сатурн", "Уран", "Нептун", "Плутон")

# Личные точки карты: по ним и читаются транзиты
PERSONAL = ("Солнце", "Луна", "Меркурий", "Венера", "Марс", "Асцендент", "Середина неба")

# Допуск для транзита уже, чем для натального аспекта: иначе список
# превращается в простыню, в которой всё важно и потому ничего не важно
TRANSIT_ORB = 2.0


@dataclass(frozen=True)
class Transit:
    moving: str  # планета, которая идёт сейчас
    natal: str  # точка натальной карты
    kind: str
    exact: float
    retrograde: bool = False

    def __str__(self) -> str:
        mark = " (R)" if self.retrograde else ""
        return f"{self.moving}{mark} {self.kind} к натальному «{self.natal}» ({self.exact:.1f}°)"


def transits(
    natal: dict[str, Position], moment: datetime, *, orb: float = TRANSIT_ORB
) -> list[Transit]:
    """Медленные планеты на градусах личных точек карты."""
    found = []
    for planet in planets(moment):
        if planet.name not in SLOW:
            continue
        for name, position in natal.items():
            if name not in PERSONAL:
                continue
            gap = abs(planet.position.degrees - position.degrees) % 360
            gap = min(gap, 360 - gap)
            for kind, angle, _ in ASPECTS:
                if abs(gap - angle) <= orb:
                    found.append(
                        Transit(planet.name, name, kind, abs(gap - angle), planet.retrograde)
                    )
                    break
    return sorted(found, key=lambda transit: transit.exact)
