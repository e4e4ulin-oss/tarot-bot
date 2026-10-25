"""Обновление схемы у базы, которая уже работает.

На сервере таблицы созданы давно, а create_all() существующие таблицы не трогает —
значит новые колонки нужно дописывать самим, не потеряв накопленные заявки.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from bot.db import create_engine, create_session_factory, init_models
from bot.db.repo import Repo

OLD_SCHEMA = (
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64), "
    "first_name VARCHAR(128), created_at DATETIME)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, question TEXT, "
    "topic VARCHAR(64), contact VARCHAR(128), status VARCHAR(16), admin_id INTEGER, "
    "admin_message_id INTEGER, created_at DATETIME, answered_at DATETIME)",
)


async def columns(engine, table: str) -> set[str]:
    async with engine.begin() as conn:
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
    return {row[1] for row in rows}


@pytest_asyncio.fixture
async def old_database(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        for statement in OLD_SCHEMA:
            await conn.execute(text(statement))
        await conn.execute(
            text(
                "INSERT INTO orders (id, user_id, question, status, created_at) "
                "VALUES (1, 5, 'старая заявка', 'NEW', '2026-01-01 00:00:00')"
            )
        )
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_missing_columns_are_added_without_losing_orders(old_database):
    assert {"tier", "followups_used"} & await columns(old_database, "orders") == set()

    await init_models(old_database)

    assert {"tier", "followups_used"} <= await columns(old_database, "orders")
    async with old_database.begin() as conn:
        kept = (
            await conn.execute(
                text("SELECT question, tier, followups_used FROM orders WHERE id = 1")
            )
        ).first()
    # Уточнения у старой заявки ещё не потрачены — счётчик начинается с нуля.
    assert kept == ("старая заявка", None, 0)


@pytest.mark.asyncio
async def test_running_twice_changes_nothing(old_database):
    await init_models(old_database)
    await init_models(old_database)

    assert "tier" in await columns(old_database, "orders")


@pytest.mark.asyncio
async def test_new_orders_work_on_the_upgraded_database(old_database):
    """После обновления схемы заявка с уровнем сохраняется как обычно."""
    await init_models(old_database)
    factory = create_session_factory(old_database)

    async with factory() as session:
        repo = Repo(session)
        await repo.upsert_user(5, None, "Клиент")
        order = await repo.create_order(
            user_id=5, question="Новая заявка", topic="love", contact=None, tier="full"
        )

    assert order.tier == "full"


@pytest.mark.asyncio
async def test_empty_database_is_created_from_scratch(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")

    await init_models(engine)

    assert "tier" in await columns(engine, "orders")
    await engine.dispose()


@pytest.mark.asyncio
async def test_rebuilt_tables_keep_their_rows(tmp_path: Path):
    """Ключ в SQLite на месте не меняется, поэтому две таблицы пересобираются.

    Проверяем самое неприятное: при пересборке не потерялись ни люди, ни их
    подписка, ни кэш картинок — и всё досталось арендатору номер один.
    """
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'live.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64), "
                "first_name VARCHAR(128), created_at DATETIME, "
                "daily_subscribed BOOLEAN DEFAULT 0 NOT NULL)"
            )
        )
        await conn.execute(
            text("CREATE TABLE card_images (card_id VARCHAR(32) PRIMARY KEY, file_id VARCHAR(256))")
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, username, first_name, created_at, daily_subscribed) "
                "VALUES (42, 'клиент', 'Клиент', '2026-01-01 00:00:00', 1)"
            )
        )
        await conn.execute(
            text("INSERT INTO card_images (card_id, file_id) VALUES ('major_00', 'file-42')")
        )

    await init_models(engine)

    assert "tenant_id" in await columns(engine, "users")
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session, 1)
        user = await repo.get_user(42)
        assert user is not None and user.first_name == "Клиент"
        assert await repo.is_daily_subscribed(42) is True
        assert await repo.card_file_ids(["major_00"]) == {"major_00": "file-42"}

    # Временная таблица убрана за собой
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()
    assert not [name for (name,) in rows if name.endswith("_pre_tenant")]

    await engine.dispose()


@pytest.mark.asyncio
async def test_interrupted_rebuild_is_finished_on_the_next_start(tmp_path: Path):
    """Пересборка могла оборваться после переименования таблицы.

    Тогда данные лежат в отставленной таблице, а новая — пустая. Следующий
    запуск обязан это заметить и долить, а не оставить клиентов в стороне.
    """
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'broken.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users_pre_tenant (id INTEGER PRIMARY KEY, username VARCHAR(64), "
                "first_name VARCHAR(128), created_at DATETIME, "
                "daily_subscribed BOOLEAN DEFAULT 0 NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users_pre_tenant (id, first_name, created_at, daily_subscribed) "
                "VALUES (7, 'Потеряшка', '2026-01-01 00:00:00', 1)"
            )
        )

    await init_models(engine)

    factory = create_session_factory(engine)
    async with factory() as session:
        user = await Repo(session, 1).get_user(7)
    assert user is not None and user.first_name == "Потеряшка"

    await engine.dispose()


# Так таблица арендаторов выглядела, когда платным был только авторский разбор:
# ни цены натального разбора, ни ссылки на мини-приложение в ней ещё нет
TENANTS_BEFORE_NATAL = (
    "CREATE TABLE tenants (id INTEGER PRIMARY KEY, slug VARCHAR(32) UNIQUE, "
    "title VARCHAR(128), bot_token VARCHAR(64) UNIQUE, bot_id BIGINT UNIQUE, "
    "admin_chat_id BIGINT DEFAULT 0 NOT NULL, admin_ids JSON, "
    "author_name VARCHAR(64), currency VARCHAR(8), price_short INTEGER, "
    "price_base INTEGER, price_full INTEGER, free_auto_readings INTEGER, "
    "free_needs_daily BOOLEAN, auto_reading_price INTEGER, "
    "active BOOLEAN DEFAULT 1 NOT NULL, created_at DATETIME)"
)


@pytest.mark.asyncio
async def test_a_column_forgotten_in_the_list_is_added_from_the_model(tmp_path: Path):
    """Цена натального разбора появилась в модели позже самой таблицы.

    Такую колонку никто не вписал в список вручную — и бот на сервере падал
    на первом же запросе к арендатору. Схема обязана дотягиваться до модели
    сама, иначе каждое новое поле — это новое падение.
    """
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'tenants.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(TENANTS_BEFORE_NATAL))
        await conn.execute(
            text(
                "INSERT INTO tenants (id, slug, title, bot_token, bot_id, admin_ids, "
                "author_name, currency, price_short, price_base, price_full, "
                "free_auto_readings, free_needs_daily, auto_reading_price, created_at) "
                "VALUES (1, 'origin', 'Свой бот', 'token-1', 8217320563, '[]', "
                "'таролог', '₽', 500, 700, 1000, 3, 1, 369, '2026-01-01 00:00:00')"
            )
        )

    await init_models(engine)

    assert {"natal_reading_price", "miniapp_url", "payment_provider_token"} <= await columns(
        engine, "tenants"
    )

    # Старый арендатор на месте, а новая цена взялась из значения по умолчанию
    async with engine.begin() as conn:
        kept = (
            await conn.execute(
                text("SELECT title, natal_reading_price, miniapp_url FROM tenants WHERE id = 1")
            )
        ).first()
    assert kept == ("Свой бот", 990, "")

    await engine.dispose()


@pytest.mark.asyncio
async def test_the_tenant_reads_back_through_the_model(tmp_path: Path):
    """Проверка не колонки, а того самого запроса, который падал на сервере."""
    from sqlalchemy import select

    from bot.db.models import Tenant

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'tenants.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(TENANTS_BEFORE_NATAL))

    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        await session.execute(select(Tenant).limit(1))

    await engine.dispose()


@pytest.mark.asyncio
async def test_the_model_pass_does_not_cancel_the_rebuild(tmp_path: Path):
    """Колонка арендатора у людей входит в ключ, а ключ дописать нельзя.

    Если дописать её обычным ALTER, пересборка решит, что всё уже сделано, и
    первичный ключ останется старым. Такую таблицу второй проход не трогает.
    """
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'keys.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64), "
                "first_name VARCHAR(128), created_at DATETIME)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, first_name, created_at) "
                "VALUES (9, 'Клиент', '2026-01-01')"
            )
        )

    await init_models(engine)

    async with engine.begin() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(users)"))).all()
    # Ключ собран из двух колонок: арендатор и человек
    assert {row[1] for row in rows if row[5]} == {"tenant_id", "id"}

    await engine.dispose()


@pytest.mark.asyncio
async def test_the_referral_columns_reach_a_live_users_table(tmp_path: Path):
    """Приглашения и промокоды добавили два поля человеку и две таблицы.

    У работающего бота таблица users уже есть, и create_all её не тронет.
    Ровно на этом бот однажды и перестал запускаться.
    """
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'grown.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users (tenant_id INTEGER NOT NULL, id INTEGER NOT NULL, "
                "username VARCHAR(64), first_name VARCHAR(128), created_at DATETIME, "
                "daily_subscribed BOOLEAN DEFAULT 0 NOT NULL, "
                "PRIMARY KEY (tenant_id, id))"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users (tenant_id, id, first_name, created_at, daily_subscribed) "
                "VALUES (1, 5, 'Старый клиент', '2026-01-01 00:00:00', 1)"
            )
        )

    await init_models(engine)

    assert {"invited_by", "bonus_readings"} <= await columns(engine, "users")

    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session)
        user = await repo.get_user(5)
        assert user is not None and user.first_name == "Старый клиент"
        # Ни пригласившего, ни бонусов у старого клиента нет — и это не None
        assert user.invited_by is None and user.bonus_readings == 0
        assert await repo.add_bonus(5, 2) == 2

        # Таблицы промокодов создались сами
        await repo.add_promo("ОСЕНЬ", readings=1)
        assert len(await repo.promos()) == 1

    await engine.dispose()
