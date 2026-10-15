"""Разбор того, что человек написал о себе, и сборка этого обратно в текст.

Отдельно от обработчиков: правила разбора даты проверяются тестами, а не
перепиской с ботом.
"""

from __future__ import annotations

from datetime import date, datetime

SKIP = {"не знаю", "незнаю", "не помню", "пропустить", "-", "—", "нет"}

DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y")
TIME_FORMATS = ("%H:%M", "%H.%M", "%H %M")

MIN_YEAR = 1900
CITY_LIMIT = 64


def skipped(text: str) -> bool:
    return text.strip().lower() in SKIP


def parse_date(text: str) -> date | None:
    """Дата рождения в привычных написаниях. Будущее и древность — не дата."""
    raw = text.strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        if MIN_YEAR <= parsed.year <= date.today().year:
            return parsed
        return None
    return None


def parse_time(text: str) -> str | None:
    """Время рождения строкой «ЧЧ:ММ»: часового пояса мы всё равно не знаем."""
    raw = text.strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def parse_city(text: str) -> str | None:
    city = " ".join(text.split())
    if not city or len(city) > CITY_LIMIT:
        return None
    return city


def describe(user) -> str:
    """Что бот знает о человеке — его же словами, без пустых строк."""
    lines = []
    if user is not None and user.birth_date:
        lines.append(f"Дата рождения: {user.birth_date.strftime('%d.%m.%Y')}")
    if user is not None and user.birth_time:
        lines.append(f"Время рождения: {user.birth_time}")
    if user is not None and user.birth_city:
        lines.append(f"Город рождения: {user.birth_city}")
    if user is not None and user.city:
        lines.append(f"Сейчас живёт: {user.city}")
    return "\n".join(lines)
