from random import Random

import pytest
import pytest_asyncio

from bot.db import OrderStatus, Repo, create_engine, create_session_factory, init_models
from bot.tarot import draw_spread, get_spread, serialize


@pytest_asyncio.fixture
async def repo():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        yield Repo(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_order_lifecycle(repo):
    await repo.upsert_user(1, "nastya_client", "Клиент")
    order = await repo.create_order(
        user_id=1, question="Что с работой?", topic="work", contact=None
    )

    assert order.status is OrderStatus.NEW
    assert order.is_open
    assert (await repo.open_order_for_user(1)).id == order.id

    await repo.set_order_status(order, OrderStatus.IN_PROGRESS, admin_id=99)
    assert order.admin_id == 99
    assert order.is_open

    await repo.set_order_status(order, OrderStatus.ANSWERED)
    assert order.answered_at is not None
    assert not order.is_open
    assert await repo.open_order_for_user(1) is None


@pytest.mark.asyncio
async def test_open_orders_listing_ignores_closed(repo):
    await repo.upsert_user(1, None, "A")
    await repo.upsert_user(2, None, "B")
    first = await repo.create_order(user_id=1, question="q1", topic=None, contact=None)
    second = await repo.create_order(user_id=2, question="q2", topic=None, contact=None)
    await repo.set_order_status(first, OrderStatus.CANCELLED)

    open_ids = [order.id for order in await repo.open_orders()]
    assert open_ids == [second.id]


@pytest.mark.asyncio
async def test_readings_are_saved_and_counted(repo):
    await repo.upsert_user(1, None, "A")
    spread = get_spread("ptf")
    drawn = draw_spread(spread, rng=Random(1))

    await repo.save_reading(
        user_id=1,
        spread_key=spread.key,
        question="Вопрос",
        cards=serialize(drawn),
        interpretation="Текст",
        ai_used=True,
    )

    assert await repo.count_readings_today(1) == 1
    assert await repo.count_readings_today(2) == 0

    last = await repo.last_readings(1)
    assert last[0].cards == serialize(drawn)
    assert last[0].ai_used is True


@pytest.mark.asyncio
async def test_upsert_user_updates_profile(repo):
    await repo.upsert_user(5, "old", "Старое имя")
    user = await repo.upsert_user(5, "new", "Новое имя")
    assert user.username == "new"
    assert user.display == "Новое имя (@new)"


@pytest.mark.asyncio
async def test_stats(repo):
    await repo.upsert_user(1, None, "A")
    await repo.create_order(user_id=1, question="q", topic=None, contact=None)
    stats = await repo.stats()
    assert stats == {"users": 1, "readings": 0, "orders": 1, "open_orders": 1}
