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
    ("users", "daily_subscribed", "BOOLEAN DEFAULT 0 NOT NULL"),
    # Арендатор: единица — бот, с которого всё начиналось
    ("readings", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("orders", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("events", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("daily_cards", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("payments", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
)

# Таблицы, где арендатор входит в первичный ключ. Такой ключ в SQLite не
# меняется на месте: таблицу приходится пересобирать и переливать.
REBUILT_FOR_TENANT: tuple[str, ...] = ("users", "card_images")


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


def _park_tables_without_tenant_key(conn) -> list[str]:
    """Отставляет в сторону таблицы, чей ключ ещё не знает об арендаторе."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    parked = []
    for table in REBUILT_FOR_TENANT:
        # Прошлая пересборка могла оборваться после переименования: тогда данные
        # лежат в отставленной таблице, а новая пустая. Доливаем, а не теряем.
        if f"{table}_pre_tenant" in tables:
            logger.warning("Нахожу недоделанную пересборку %s — доливаю", table)
            if table in tables:
                conn.execute(text(f"DROP TABLE {table}"))
            parked.append(table)
            continue
        if table not in tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "tenant_id" in columns:
            continue
        logger.info("Пересобираю %s под арендаторов", table)
        conn.execute(text(f"ALTER TABLE {table} RENAME TO {table}_pre_tenant"))
        parked.append(table)
    return parked


# Чем заполнить обязательную колонку, которой в старой таблице не было.
# Схема росла постепенно, и строка пятилетней давности может не знать о поле,
# появившемся позже; без подстановки перелив упал бы на NOT NULL.
FILLERS: dict[str, str] = {"DATETIME": "CURRENT_TIMESTAMP", "INTEGER": "0", "BOOLEAN": "0"}


def _refill_rebuilt_tables(conn, parked: list[str]) -> None:
    """Переливает отставленные строки в новую таблицу, отдав их арендатору 1."""
    inspector = inspect(conn)
    for table in parked:
        old = f"{table}_pre_tenant"
        had = [column["name"] for column in inspector.get_columns(old)]
        fresh = {column["name"]: column for column in inspector.get_columns(table)}

        names = ["tenant_id", *had]
        values = ["1", *had]
        for name, column in fresh.items():
            if name in names or column["nullable"]:
                continue
            kind = str(column["type"]).split("(")[0].upper()
            names.append(name)
            values.append(FILLERS.get(kind, "''"))

        conn.execute(
            text(
                f"INSERT INTO {table} ({', '.join(names)}) "
                f"SELECT {', '.join(values)} FROM {old}"
            )
        )
        conn.execute(text(f"DROP TABLE {old}"))
        logger.info("Таблица %s пересобрана", table)


async def init_models(engine: AsyncEngine) -> None:
    try:
        await _upgrade(engine)
    except Exception:
        # Бот не поднимется с недостроенной схемой, и молча гадать по трейсу
        # на сервере неудобно: пишем, что именно не доехало
        logger.exception("Схему базы обновить не удалось")
        raise


async def _upgrade(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)
        parked = await conn.run_sync(_park_tables_without_tenant_key)
        await conn.run_sync(Base.metadata.create_all)
        if parked:
            await conn.run_sync(_refill_rebuilt_tables, parked)
