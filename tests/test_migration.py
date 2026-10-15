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
