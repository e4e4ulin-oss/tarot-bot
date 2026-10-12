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
async def test_missing_column_is_added_without_losing_orders(old_database):
    assert "tier" not in await columns(old_database, "orders")

    await init_models(old_database)

    assert "tier" in await columns(old_database, "orders")
    async with old_database.begin() as conn:
        kept = (await conn.execute(text("SELECT question, tier FROM orders WHERE id = 1"))).first()
    assert kept == ("старая заявка", None)


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
