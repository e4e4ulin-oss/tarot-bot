"""Воронка: считаем людей, а не нажатия, и показываем переходы между шагами."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from bot import analytics
from bot.config import Settings
from bot.db import Event, create_engine, create_session_factory, init_models
from bot.db.repo import Repo
from bot.handlers.admin import _funnel_table, _period_label, _tiers_block

NAMES = tuple(name for name, _ in analytics.FUNNEL)


def settings(**overrides) -> Settings:
    return Settings(BOT_TOKEN="t:t", _env_file=None, **overrides)


@pytest_asyncio.fixture
async def repo():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        yield Repo(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_steps_count_as_one_person(repo):
    """Человек может открыть авторский разбор пять раз — в воронке он один."""
    for _ in range(5):
        await repo.log_event(1, analytics.AUTHOR_OPEN)
    await repo.log_event(2, analytics.AUTHOR_OPEN)

    assert (await repo.funnel(NAMES))[analytics.AUTHOR_OPEN] == 2


@pytest.mark.asyncio
async def test_steps_nobody_reached_are_zero_not_missing(repo):
    await repo.log_event(1, analytics.START)

    steps = await repo.funnel(NAMES)

    assert steps[analytics.START] == 1
    assert steps[analytics.ORDER_SENT] == 0
    assert set(steps) == set(NAMES)


@pytest.mark.asyncio
async def test_period_cuts_off_old_events(repo):
    await repo.log_event(1, analytics.START)
    old = await repo.session.get(Event, 1)
    old.created_at = datetime.now(UTC) - timedelta(days=10)
    await repo.session.commit()
    await repo.log_event(2, analytics.START)

    week = await repo.funnel(NAMES, datetime.now(UTC) - timedelta(days=7))
    forever = await repo.funnel(NAMES)

    assert week[analytics.START] == 1
    assert forever[analytics.START] == 2


@pytest.mark.asyncio
async def test_orders_counted_by_level(repo):
    await repo.upsert_user(1, None, "Клиент")
    for tier in ("base", "base", "full", None):
        await repo.create_order(user_id=1, question="?", topic=None, contact=None, tier=tier)

    assert await repo.orders_by_tier() == {"base": 2, "full": 1, "не выбран": 1}


def test_table_shows_transition_from_the_previous_step():
    steps = {
        analytics.START: 20,
        analytics.AUTO_OPEN: 10,
        analytics.AUTO_READING: 5,
        analytics.AUTHOR_OPEN: 0,
        analytics.TIER_CHOSEN: 0,
        analytics.ORDER_SENT: 0,
        analytics.ORDER_ANSWERED: 0,
    }

    table = _funnel_table(steps)

    assert "50%" in table  # 10 из 20
    assert "Зашли в бота" in table
    # У первого шага процента нет: ему не от чего считаться.
    assert table.splitlines()[0].rstrip().endswith("20")


def test_table_survives_an_empty_funnel():
    table = _funnel_table(dict.fromkeys(NAMES, 0))

    assert len(table.splitlines()) == len(analytics.FUNNEL)
    assert "%" not in table  # делить не на что


def test_money_is_counted_per_level():
    block = _tiers_block(settings(), {"base": 2, "full": 1})

    assert "Базовый: 2 × 700 ₽ = 1400 ₽" in block
    assert "Полный: 1 × 1000 ₽ = 1000 ₽" in block
    assert "2400 ₽" in block


def test_orders_without_a_level_bring_no_money():
    block = _tiers_block(settings(), {None: 3})

    assert "Без уровня: 3" in block
    assert "Итого" not in block


@pytest.mark.parametrize(("days", "label"), [(0, "всё время"), (1, "сутки"), (30, "30 дн.")])
def test_period_label(days, label):
    assert _period_label(days) == label
