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
from datetime import datetime, timedelta

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


def _aspect_between(
    first: float, second: float, *, wider: bool = False
) -> tuple[str, float] | None:
    """Какой угол стоит между двумя точками карты и насколько он точен.

    Допуск шире там, где участвует главное — светила и углы карты. Это не
    поблажка, а порядок чтения: их связи замечают первыми.
    """
    gap = abs(first - second) % 360
    gap = min(gap, 360 - gap)
    for kind, angle, orb in ASPECTS:
        if abs(gap - angle) <= orb + (WIDER if wider else 0):
            return kind, abs(gap - angle)
    return None


def aspects(bodies: list[Planet]) -> list[Aspect]:
    """Значимые углы между планетами, от точных к широким."""
    found = []
    for index, first in enumerate(bodies):
        for second in bodies[index + 1 :]:
            hit = _aspect_between(
                first.position.degrees,
                second.position.degrees,
                wider=first.name in LUMINARIES or second.name in LUMINARIES,
            )
            if hit is not None:
                found.append(Aspect(first.name, second.name, *hit))
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


# Докуда заглядывать вперёд и назад, когда ищем границы транзита. Полтора
# года покрывают все возвраты медленных планет из-за попятного хода: дольше
# на одном градусе не стоит никто.
HORIZON = 540
CODE_BY_NAME = {name: code for code, name in PLANETS}


@dataclass(frozen=True)
class Timing:
    """Жизнь одного транзита во времени."""

    exact: list[datetime]  # когда аспект точен, по порядку
    entered: datetime | None  # когда вошёл в орб
    leaves: datetime | None  # когда выйдет


def _degrees_of(code: str, moment: datetime) -> float:
    """Долгота планеты по имени в ephem — для поиска сроков транзита."""
    return math.degrees(ephem.Ecliptic(getattr(ephem, code)(ephem.Date(moment))).lon) % 360


def _signed(value: float) -> float:
    """Угол, приведённый к отрезку от минус ста восьмидесяти до ста восьмидесяти."""
    return (value + 180) % 360 - 180


def _branch(code: str, natal: float, angle: float, moment: datetime) -> int:
    """Аспект точен в двух положениях планеты; выбираем то, к которому ближе.

    Без этого выбора функция расстояния не меняет знак на точном аспекте, и
    момент точности не находится вовсе.
    """
    delta = _signed(_degrees_of(code, moment) - natal)
    return 1 if abs(_signed(delta - angle)) <= abs(_signed(delta + angle)) else -1


def _offset(code: str, natal: float, angle: float, moment: datetime, branch: int = 1) -> float:
    """Насколько далеко до точного аспекта, со знаком."""
    return _signed(_signed(_degrees_of(code, moment) - natal) - branch * angle)


def _refine(
    code: str, natal: float, angle: float, left: datetime, right: datetime, branch: int
) -> datetime:
    """Делением пополам доводит момент точного аспекта до часа."""
    for _ in range(12):
        middle = left + (right - left) / 2
        if _offset(code, natal, angle, left, branch) * _offset(
            code, natal, angle, middle, branch
        ) <= 0:
            right = middle
        else:
            left = middle
    return left + (right - left) / 2


def timing(
    planet: str,
    natal: float,
    angle: float,
    now: datetime,
    *,
    orb: float = TRANSIT_ORB,
    horizon: int = HORIZON,
) -> Timing:
    """Когда транзит был точен, когда вошёл в орб и когда выйдет.

    Считается перебором по эфемеридам с шагом в сутки: медленная планета за
    сутки проходит доли градуса и орб перескочить не может.
    """
    code = CODE_BY_NAME[planet]
    branch = _branch(code, natal, angle, now)
    days = [now + timedelta(days=step) for step in range(-horizon, horizon + 1)]
    offsets = [_offset(code, natal, angle, day, branch) for day in days]

    exact = [
        _refine(code, natal, angle, days[index], days[index + 1], branch)
        for index in range(len(days) - 1)
        # Знак меняется только на точном аспекте; скачок через полкруга —
        # это другая сторона зодиака, а не точность
        if offsets[index] * offsets[index + 1] <= 0
        and abs(offsets[index] - offsets[index + 1]) < 90
    ]

    inside = [index for index, value in enumerate(offsets) if abs(value) <= orb]
    entered = leaves = None
    if inside:
        # Нас интересует тот заход в орб, в котором мы сейчас
        here = horizon
        first = here
        while first - 1 in inside:
            first -= 1
        last = here
        while last + 1 in inside:
            last += 1
        entered = days[first] if first > 0 else None
        leaves = days[last] if last < len(days) - 1 else None

    return Timing(exact=exact, entered=entered, leaves=leaves)


# Управители знаков. Берутся современные; у трёх знаков в скобках оставлен
# старый управитель — традиция им пользуется до сих пор, и в разборе это
# иногда заметно.
RULERS: dict[str, str] = {
    "Овен": "Марс",
    "Телец": "Венера",
    "Близнецы": "Меркурий",
    "Рак": "Луна",
    "Лев": "Солнце",
    "Дева": "Меркурий",
    "Весы": "Венера",
    "Скорпион": "Плутон",
    "Стрелец": "Юпитер",
    "Козерог": "Сатурн",
    "Водолей": "Уран",
    "Рыбы": "Нептун",
}

OLD_RULERS: dict[str, str] = {"Скорпион": "Марс", "Водолей": "Сатурн", "Рыбы": "Юпитер"}

ELEMENTS: tuple[str, ...] = ("огонь", "земля", "воздух", "вода")
MODES: tuple[str, ...] = ("кардинальный", "фиксированный", "мутабельный")

# Фазы Луны по расстоянию от Солнца: восемь делений круга
PHASES: tuple[str, ...] = (
    "новолуние",
    "молодая луна",
    "первая четверть",
    "прибывающая луна",
    "полнолуние",
    "убывающая луна",
    "последняя четверть",
    "старая луна",
)


def element_of(sign: str) -> str:
    return ELEMENTS[SIGNS.index(sign) % 4]


def mode_of(sign: str) -> str:
    return MODES[SIGNS.index(sign) % 3]


def balance(bodies: list[Planet]) -> tuple[dict[str, int], dict[str, int]]:
    """Сколько в карте каждой стихии и каждого креста.

    Перекос виден сразу и объясняет человека быстрее, чем три абзаца о
    планетах: «земли много, воздуха нет» — это уже характер.
    """
    elements = dict.fromkeys(ELEMENTS, 0)
    modes = dict.fromkeys(MODES, 0)
    for planet in bodies:
        elements[element_of(planet.position.sign)] += 1
        modes[mode_of(planet.position.sign)] += 1
    return elements, modes


def moon_phase(moment: datetime) -> str:
    """Фаза Луны при рождении — по углу между Луной и Солнцем."""
    gap = (moon(moment).degrees - sun(moment).degrees) % 360
    return PHASES[int((gap + 22.5) % 360 // 45)]


def north_node(moment: datetime) -> Position:
    """Средний восходящий узел Луны.

    Узел — не тело, а точка пересечения орбит, и эфемерид для него нет.
    Считается формулой среднего движения: узел идёт назад, полный круг за
    восемнадцать с половиной лет.
    """
    julian = ephem.Date(moment) + 2415020.0  # ephem считает дни от 1899 года
    century = (julian - 2451545.0) / 36525.0
    degrees = (
        125.04452
        - 1934.136261 * century
        + 0.0020708 * century**2
        + century**3 / 450000.0
    )
    return Position(degrees % 360)


# Углы карты — не планеты, а направления: асцендент это то, каким человека
# видят, середина неба — куда он идёт
ANGLES: tuple[str, str] = ("Асцендент", "Середина неба")


def angle_aspects(
    bodies: list[Planet], ascendant: Position | None, midheaven: Position | None
) -> list[Aspect]:
    """Аспекты планет к углам карты.

    Планета на углу видна в человеке сразу, без расспросов, поэтому допуск
    здесь такой же широкий, как у светил.
    """
    found = []
    for name, point in zip(ANGLES, (ascendant, midheaven), strict=True):
        if point is None:
            continue
        for planet in bodies:
            hit = _aspect_between(planet.position.degrees, point.degrees, wider=True)
            if hit is not None:
                found.append(Aspect(planet.name, name, *hit))
    return sorted(found, key=lambda aspect: aspect.exact)


def cusp(house: int, ascendant: Position) -> Position:
    """Начало дома в равнодомной системе: от асцендента по тридцать градусов."""
    return Position((ascendant.degrees + SIGN * (house - 1)) % 360)


@dataclass(frozen=True)
class Ruler:
    house: int
    sign: str  # знак на начале дома
    planet: str  # его управитель
    position: Position | None = None  # где стоит сам управитель
    where: int | None = None  # и в каком он доме
    old: str | None = None  # старый управитель знака, если он другой

    def __str__(self) -> str:
        line = f"{self.house} дом ({self.sign}) — {self.planet}"
        if self.position is not None:
            line += f": {self.position}"
        if self.where:
            line += f", {self.where} дом"
        if self.old:
            line += f"; старый управитель — {self.old}"
        return line


def rulers(ascendant: Position, bodies: list[Planet]) -> list[Ruler]:
    """Хозяева домов и то, где они сами стоят.

    Дом говорит о теме, управитель — о том, чем эта тема живёт: куда уходят
    силы дома и откуда приходят. Без этого шага дома остаются списком тем,
    между которыми нет связи.
    """
    found = {planet.name: planet for planet in bodies}
    out = []
    for house in range(1, 13):
        sign = cusp(house, ascendant).sign
        planet = found.get(RULERS[sign])
        out.append(
            Ruler(
                house=house,
                sign=sign,
                planet=RULERS[sign],
                position=planet.position if planet else None,
                where=house_of(planet.position, ascendant) if planet else None,
                old=OLD_RULERS.get(sign),
            )
        )
    return out
