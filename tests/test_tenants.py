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
