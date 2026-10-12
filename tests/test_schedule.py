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
        (msk(12, 19, 30), 1, msk(13, 10)),  # заявка вечером
        (msk(13, 3), 3, msk(13, 12)),  # заявка ночью
        (msk(17, 12), 1, msk(19, 10)),  # выходные целиком пропускаются
    ],
)
def test_deadline_spends_only_working_hours(moment, hours, expected):
    assert deadline(settings(), moment, hours) == expected


@pytest.mark.parametrize(
    ("moment", "hours", "expected"),
    [
        # Укладывается 75% и больше — доводим сегодня, выйдя за 18:00
        (msk(12, 16, 30), 2, msk(12, 18, 30)),
        (msk(12, 17, 15), 1, msk(12, 18, 15)),
        (msk(12, 15, 45), 3, msk(12, 18, 45)),
        # Меньше 75% — перенос на следующее утро, отсчёт с 9:00
        (msk(12, 17), 2, msk(13, 11)),
        (msk(12, 17, 30), 2, msk(13, 11)),
        (msk(12, 17, 45), 1, msk(13, 10)),
        (msk(16, 17, 30), 3, msk(19, 12)),  # пятница вечером → понедельник
    ],
)
def test_small_overflow_is_finished_today_a_big_one_moves(moment, hours, expected):
    assert deadline(settings(), moment, hours) == expected


def test_threshold_is_configurable():
    """При 100% за закрытие не выходим вовсе, при 50% — терпим больший хвост."""
    strict = settings(WORK_OVERFLOW_SHARE=1.0)
    loose = settings(WORK_OVERFLOW_SHARE=0.5)

    assert deadline(strict, msk(12, 16, 30), 2) == msk(13, 11)
    assert deadline(loose, msk(12, 17), 2) == msk(12, 19)


def test_overflow_never_exceeds_the_allowed_share():
    """Хвост за 18:00 ограничен: иначе «до 18:30» превратилось бы в «до ночи»."""
    limit = settings().work_overflow_share
    for hour in range(24):
        for minute in (0, 15, 30, 45):
            for tier_hours in (1, 2, 3):
                result = deadline(settings(), msk(12, hour, minute), tier_hours)
                if is_working_time(settings(), result):
                    continue
                closing = result.replace(hour=18, minute=0)
                overflow = (result - closing).total_seconds() / 3600
                assert 0 <= overflow <= tier_hours * (1 - limit) + 1e-9, (
                    hour,
                    minute,
                    tier_hours,
                    result,
                )


def test_working_hours_are_configurable():
    custom = settings(WORK_START_HOUR=10, WORK_END_HOUR=16)

    assert work_hours_label(custom) == "10:00–16:00"
    # 15:00 + 2 ч: до закрытия в 16:00 укладывается половина — переносим на 10:00
    assert deadline(custom, msk(12, 15), 2) == msk(13, 12)


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
