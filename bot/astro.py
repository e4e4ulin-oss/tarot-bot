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
