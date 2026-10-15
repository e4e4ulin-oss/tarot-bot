"""Арендаторы: настройки берутся у того бренда, в чей бот написали."""

import pytest
import pytest_asyncio

from bot.config import Settings
from bot.db import Tenant
from bot.db.session import create_engine, create_session_factory, init_models
from bot.tenants import active_tenants, bot_id_of, ensure_default, settings_for

BASE = dict(BOT_TOKEN="100200:abc", XAI_API_KEY="", AUTHOR_NAME="Первый")


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'tenants.db'}")
    await init_models(engine)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_current_bot_becomes_the_first_tenant(factory):
    settings = Settings(**BASE)
    async with factory() as db:
        created = await ensure_default(db, settings)
        assert created is not None
        assert created.bot_id == bot_id_of(settings.bot_token)

        # Второй вызов не плодит дубликат: бот уже заведён
        assert await ensure_default(db, settings) is None
        assert len(await active_tenants(db)) == 1


@pytest.mark.asyncio
async def test_each_tenant_brings_its_own_prices(factory):
    settings = Settings(**BASE)
    async with factory() as db:
        await ensure_default(db, settings)
        db.add(
            Tenant(
                slug="second",
                title="Второй",
                bot_token="300400:xyz",
                bot_id=300400,
                author_name="Второй",
                currency="₽",
                price_full=2500,
                auto_reading_price=499,
                free_auto_readings=1,
            )
        )
        await db.commit()
        tenants = {t.slug: t for t in await active_tenants(db)}

    first = settings_for(tenants["default"], settings)
    second = settings_for(tenants["second"], settings)

    assert first.author_name == "Первый" and second.author_name == "Второй"
    assert second.author_price_full == 2500
    assert second.auto_reading_price == 499
    assert second.free_auto_readings == 1
    # Общее остаётся общим: ключи моделей и адрес базы не подменяются
    assert second.database_url == settings.database_url


@pytest.mark.asyncio
async def test_disabled_tenant_does_not_start(factory):
    settings = Settings(**BASE)
    async with factory() as db:
        tenant = await ensure_default(db, settings)
        tenant.active = False
        await db.commit()
        assert await active_tenants(db) == []


@pytest.mark.asyncio
async def test_two_tenants_do_not_see_each_other(factory):
    """Один и тот же человек в двух ботах — два разных клиента."""
    from bot.db import Repo

    person = 777
    async with factory() as db:
        first, second = Repo(db, 1), Repo(db, 2)

        await first.upsert_user(person, "ник", "Он же")
        await second.upsert_user(person, "ник", "Он же")

        await first.save_reading(
            user_id=person, spread_key="day", question=None, cards=[],
            interpretation="", ai_used=False,
        )
        await first.set_daily_subscription(person, True)
        await first.add_payment(person, 369, "RUB", "charge-A")

        # У первого бренда расклад, подписка и платёж
        assert await first.count_auto_readings(person) == 1
        assert await first.is_daily_subscribed(person) is True
        assert (await first.payments_summary())["count"] == 1
        assert await first.count_daily_subscribers() == 1

        # У второго — ничего из этого
        assert await second.count_auto_readings(person) == 0
        assert await second.is_daily_subscribed(person) is False
        assert (await second.payments_summary())["count"] == 0
        assert await second.count_daily_subscribers() == 0


@pytest.mark.asyncio
async def test_card_cache_is_not_shared_between_bots(factory):
    """file_id выдаётся под конкретного бота: чужой кэш бесполезен."""
    from bot.db import Repo

    async with factory() as db:
        first, second = Repo(db, 1), Repo(db, 2)
        await first.save_card_file_id("major_00", "file-первого")

        assert await first.card_file_ids(["major_00"]) == {"major_00": "file-первого"}
        assert await second.card_file_ids(["major_00"]) == {}

        await second.save_card_file_id("major_00", "file-второго")
        assert await second.card_file_ids(["major_00"]) == {"major_00": "file-второго"}


@pytest.mark.asyncio
async def test_orders_and_readings_are_not_readable_across_tenants(factory):
    from bot.db import Repo

    async with factory() as db:
        first, second = Repo(db, 1), Repo(db, 2)
        order = await first.create_order(user_id=1, question="чужой вопрос", topic=None, contact=None)
        reading = await first.save_reading(
            user_id=1, spread_key="day", question=None, cards=[],
            interpretation="", ai_used=False,
        )

        assert await first.get_order(order.id) is not None
        assert await second.get_order(order.id) is None
        assert await first.get_reading(reading.id) is not None
        assert await second.get_reading(reading.id) is None
        assert await second.open_orders() == []
