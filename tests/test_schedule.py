"""Срок ответа считается в рабочие часы, а не по календарю.

Даты в тестах фиксированные: 12 октября 2026 — понедельник, 16-е — пятница,
17-е — суббота.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.config import Settings
from bot.schedule import (
    deadline,
    format_moment,
    is_working_time,
    start_of_work,
    work_hours_label,
)

MSK = ZoneInfo("Europe/Moscow")


def settings(**overrides) -> Settings:
    return Settings(BOT_TOKEN="t:t", _env_file=None, **overrides)


def msk(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=MSK)


@pytest.mark.parametrize(
    ("moment", "working"),
    [
        (msk(12, 9), True),  # понедельник, открытие
        (msk(12, 17, 59), True),
        (msk(12, 18), False),  # ровно 18:00 — уже нерабочее
        (msk(12, 8, 59), False),
        (msk(17, 12), False),  # суббота
        (msk(18, 12), False),  # воскресенье
    ],
)
def test_working_time_boundaries(moment, working):
    assert is_working_time(settings(), moment) is working


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (msk(12, 10), msk(12, 10)),  # уже рабочее время — отсчёт идёт сразу
        (msk(12, 7), msk(12, 9)),  # утром ждём открытия того же дня
        (msk(12, 19), msk(13, 9)),  # вечером — следующий рабочий день
        (msk(16, 19), msk(19, 9)),  # вечер пятницы → понедельник
        (msk(17, 12), msk(19, 9)),  # суббота → понедельник
    ],
)
def test_counting_starts_at_the_next_working_moment(moment, expected):
    assert start_of_work(settings(), moment) == expected


@pytest.mark.parametrize(
    ("moment", "hours", "expected"),
    [
        (msk(12, 10), 2, msk(12, 12)),  # обычный случай внутри дня
        (msk(12, 16), 2, msk(12, 18)),  # ровно до закрытия
        (msk(12, 17, 50), 2, msk(13, 10, 50)),  # остаток часа переносится на утро
        (msk(12, 19, 30), 1, msk(13, 10)),  # заявка вечером
        (msk(13, 3), 3, msk(13, 12)),  # заявка ночью
        (msk(16, 17, 30), 3, msk(19, 11, 30)),  # пятница вечером → понедельник
        (msk(17, 12), 1, msk(19, 10)),  # выходные целиком пропускаются
    ],
)
def test_deadline_spends_only_working_hours(moment, hours, expected):
    assert deadline(settings(), moment, hours) == expected


def test_deadline_never_lands_outside_working_hours():
    for hour in range(0, 24):
        for tier_hours in (1, 2, 3):
            result = deadline(settings(), msk(12, hour), tier_hours)
            assert is_working_time(settings(), result) or result.hour == 18


def test_working_hours_are_configurable():
    custom = settings(WORK_START_HOUR=10, WORK_END_HOUR=16)

    assert work_hours_label(custom) == "10:00–16:00"
    assert deadline(custom, msk(12, 15), 2) == msk(13, 11)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (msk(12, 14, 30), "сегодня в 14:30"),
        (msk(13, 10), "завтра в 10:00"),
        (msk(13, 9), "завтра в 9:00"),  # без ведущего нуля
        (msk(14, 10), "в среду в 10:00"),
        (msk(19, 10), "19 октября в 10:00"),
    ],
)
def test_moment_reads_like_a_person_wrote_it(moment, expected):
    assert format_moment(settings(), moment, reference=msk(12, 12)) == expected
