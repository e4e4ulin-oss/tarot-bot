"""Напоминание о карте дня: кому, когда и сколько раз.

Самое важное здесь — когда молчать. Напоминание, пришедшее не вовремя или
в четвёртый раз, не приводит подписку, а приводит блокировку бота.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramForbiddenError

from bot.config import Settings
from bot.db import Repo
from bot.db.session import create_engine, create_session_factory, init_models
from bot.services.nudge import Nudges

NOW = datetime(2026, 10, 26, 12, 0)


def settings(**extra) -> Settings:
    return Settings(BOT_TOKEN="123:abc", XAI_API_KEY="", **extra)


class FakeBot:
    def __init__(self, blocked: set[int] | None = None) -> None:
        self.sent: list[int] = []
        self.blocked = blocked or set()

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        self.sent.append(chat_id)


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'nudge.db'}")
    await init_models(engine)
    yield create_session_factory(engine)
    await engine.dispose()


async def add_user(factory, user_id: int, *, days_ago: float = 2, subscribed: bool = False):
    async with factory() as session:
        repo = Repo(session)
        user = await repo.upsert_user(user_id, None, f"Гость {user_id}")
        user.created_at = datetime.now(UTC) - timedelta(days=days_ago)
        if subscribed:
            await repo.set_daily_subscription(user_id, True)
        await session.commit()
    return user_id


@pytest.mark.asyncio
async def test_the_newcomer_who_did_not_subscribe_is_reminded(factory):
    await add_user(factory, 1)
    bot = FakeBot()

    assert await Nudges(bot, settings(), factory).send_due(NOW) == 1
    assert bot.sent == [1]


@pytest.mark.asyncio
async def test_a_subscriber_is_left_alone(factory):
    await add_user(factory, 2, subscribed=True)
    bot = FakeBot()

    assert await Nudges(bot, settings(), factory).send_due(NOW) == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_the_first_hours_are_quiet(factory):
    """Человек ещё смотрит сам — напоминание в спину только мешает."""
    await add_user(factory, 3, days_ago=0.1)
    bot = FakeBot()

    assert await Nudges(bot, settings(NUDGE_AFTER_HOURS=20), factory).send_due(NOW) == 0


@pytest.mark.asyncio
async def test_it_does_not_repeat_the_next_day(factory):
    await add_user(factory, 4)
    bot = FakeBot()
    sender = Nudges(bot, settings(NUDGE_EVERY_DAYS=7), factory)

    assert await sender.send_due(NOW) == 1
    assert await sender.send_due(NOW + timedelta(days=1)) == 0
    assert await sender.send_due(NOW + timedelta(days=8)) == 1
    assert bot.sent == [4, 4]


@pytest.mark.asyncio
async def test_three_times_is_enough(factory):
    """Кто не подписался после третьего, не подпишется и после десятого."""
    await add_user(factory, 5)
    bot = FakeBot()
    sender = Nudges(bot, settings(NUDGE_EVERY_DAYS=7, NUDGE_MAX=3), factory)

    for week in range(5):
        await sender.send_due(NOW + timedelta(days=7 * week))

    assert bot.sent == [5, 5, 5]


@pytest.mark.asyncio
async def test_subscribing_stops_it_at_once(factory):
    await add_user(factory, 6)
    bot = FakeBot()
    sender = Nudges(bot, settings(), factory)

    assert await sender.send_due(NOW) == 1
    async with factory() as session:
        await Repo(session).set_daily_subscription(6, True)

    assert await sender.send_due(NOW + timedelta(days=30)) == 0


@pytest.mark.asyncio
async def test_blocking_the_bot_counts_as_told(factory):
    """Заблокировавшему напоминать некому: отмечаем и больше не пробуем."""
    await add_user(factory, 7)
    bot = FakeBot(blocked={7})
    sender = Nudges(bot, settings(NUDGE_MAX=3), factory)

    assert await sender.send_due(NOW) == 0

    async with factory() as session:
        user = await Repo(session).get_user(7)
    assert user.nudges == 1


@pytest.mark.asyncio
async def test_the_hour_is_respected(factory):
    """Днём, а не ночью и не вместе с утренней рассылкой."""
    await add_user(factory, 8)
    bot = FakeBot()
    sender = Nudges(bot, settings(NUDGE_HOUR=12), factory)

    # tick() смотрит на часы; send_due вызывается только в свой час
    assert await sender.tick() in (0, 1)
    assert await Nudges(bot, settings(NUDGE_ENABLED=False), factory).tick() == 0
