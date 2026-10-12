"""Рабочее время и сроки ответа.

Срок разбора считается только в рабочие часы: заявка в 17:50 не должна обещать
ответ в 19:50. Если заявка пришла в нерабочее время, отсчёт начинается с начала
ближайшего рабочего дня.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings

# Понедельник–пятница. weekday(): 0 — понедельник, 6 — воскресенье.
WORKDAYS = frozenset(range(5))

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

WEEKDAYS_ACCUSATIVE = (
    "в понедельник", "во вторник", "в среду", "в четверг",
    "в пятницу", "в субботу", "в воскресенье",
)


def timezone(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.work_timezone)


def now(settings: Settings) -> datetime:
    return datetime.now(timezone(settings))


def is_working_time(settings: Settings, moment: datetime) -> bool:
    local = moment.astimezone(timezone(settings))
    if local.weekday() not in WORKDAYS:
        return False
    return settings.work_start_hour <= local.hour < settings.work_end_hour


def start_of_work(settings: Settings, moment: datetime) -> datetime:
    """Ближайший момент, с которого идёт отсчёт: сейчас или начало рабочего дня."""
    local = moment.astimezone(timezone(settings))
    if is_working_time(settings, local):
        return local

    opening = local.replace(
        hour=settings.work_start_hour, minute=0, second=0, microsecond=0
    )
    # Утром того же дня ждать до открытия, иначе — следующий рабочий день.
    if local.weekday() in WORKDAYS and local.hour < settings.work_start_hour:
        return opening

    opening += timedelta(days=1)
    while opening.weekday() not in WORKDAYS:
        opening += timedelta(days=1)
    return opening


def deadline(settings: Settings, moment: datetime, hours: int) -> datetime:
    """Момент, к которому обещан ответ: часы тратятся только в рабочее время."""
    current = start_of_work(settings, moment)
    left = timedelta(hours=hours)

    while left > timedelta():
        closing = current.replace(
            hour=settings.work_end_hour, minute=0, second=0, microsecond=0
        )
        available = closing - current
        if left <= available:
            return current + left
        left -= available
        current = start_of_work(settings, closing)

    return current


def format_moment(settings: Settings, moment: datetime, reference: datetime | None = None) -> str:
    """«сегодня в 14:30», «завтра в 10:00», «в понедельник в 10:00», «13 октября в 10:00»."""
    local = moment.astimezone(timezone(settings))
    today = (reference or now(settings)).astimezone(timezone(settings)).date()
    days = (local.date() - today).days
    # «в 9:00», а не «в 09:00»: так же, как написано про рабочие часы
    clock = f"{local.hour}:{local.minute:02d}"

    if days == 0:
        return f"сегодня в {clock}"
    if days == 1:
        return f"завтра в {clock}"
    if 2 <= days <= 6:
        return f"{WEEKDAYS_ACCUSATIVE[local.weekday()]} в {clock}"
    return f"{local.day} {MONTHS_GENITIVE[local.month - 1]} в {clock}"


def work_hours_label(settings: Settings) -> str:
    return f"{settings.work_start_hour}:00–{settings.work_end_hour}:00"
