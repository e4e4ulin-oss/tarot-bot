"""Доступ к авто-раскладу: бесплатные, подписка, оплата."""

import pytest
import pytest_asyncio

from bot.access import check, spend
from bot.config import Settings
from bot.db import Repo
from bot.db.session import create_engine, create_session_factory, init_models

USER = 500


def make_settings(**extra) -> Settings:
    return Settings(BOT_TOKEN="123:abc", XAI_API_KEY="", **extra)


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session)
        await repo.upsert_user(USER, None, "Клиент")
        yield repo
    await engine.dispose()


async def make_reading(repo: Repo) -> None:
    await repo.save_reading(
        user_id=USER, spread_key="day", question=None, cards=[], interpretation="", ai_used=False
    )


@pytest.mark.asyncio
async def test_subscription_is_required_for_free_readings(repo):
    settings = make_settings(FREE_AUTO_READINGS=3)
    assert (await check(repo, settings, USER)).reason == "needs_daily"

    await repo.set_daily_subscription(USER, True)
    access = await check(repo, settings, USER)
    assert access.allowed and access.reason == "free" and access.free_left == 3


@pytest.mark.asyncio
async def test_free_readings_run_out(repo):
    settings = make_settings(FREE_AUTO_READINGS=3)
    await repo.set_daily_subscription(USER, True)

    for expected_left in (3, 2, 1):
        access = await check(repo, settings, USER)
        assert access.allowed and access.free_left == expected_left
        await make_reading(repo)

    access = await check(repo, settings, USER)
    assert not access.allowed and access.reason == "needs_payment"


@pytest.mark.asyncio
async def test_payment_opens_exactly_one_reading(repo):
    settings = make_settings(FREE_AUTO_READINGS=0)
    await repo.add_payment(USER, 369, "RUB", "charge-1")

    access = await check(repo, settings, USER)
    assert access.allowed and access.reason == "paid"

    # Пока расклад не состоялся, оплата не списана
    assert (await check(repo, settings, USER)).allowed

    await spend(repo, access)
    assert (await check(repo, settings, USER)).reason == "needs_payment"


@pytest.mark.asyncio
async def test_payments_are_counted_for_the_owner(repo):
    await repo.add_payment(USER, 369, "RUB", "charge-2")
    await repo.add_payment(USER, 369, "RUB", "charge-3")
    assert await repo.payments_summary() == {"count": 2, "total": 738}
