"""Подключение к базе: асинхронный движок и фабрика сессий."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

logger = logging.getLogger(__name__)

# Колонки, добавленные к уже существующим таблицам. create_all() обходит таблицу,
# если она есть, поэтому у работающего бота новые поля надо дописывать самим.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("orders", "tier", "VARCHAR(16)"),
    ("orders", "followups_used", "INTEGER DEFAULT 0 NOT NULL"),
)


def _ensure_sqlite_dir(url: str) -> None:
    marker = "sqlite+aiosqlite:///"
    if not url.startswith(marker):
        return
    path = url[len(marker) :]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_engine(database_url: str) -> AsyncEngine:
    _ensure_sqlite_dir(database_url)
    return create_async_engine(database_url, echo=False, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _add_missing_columns(conn) -> None:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for table, column, column_type in ADDED_COLUMNS:
        if table not in tables:
            continue  # create_all уже создал её целиком
        if column in {existing["name"] for existing in inspector.get_columns(table)}:
            continue
        logger.info("Добавляю колонку %s.%s", table, column)
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(Base.metadata.create_all)
