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

# Колонки, для которых значение старых строк названо вручную. Остальное
# дописывается по самой модели (_add_from_model): перечислять каждое новое поле
# здесь означало бы ломать работающего бота всякий раз, когда о нём забыли.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("orders", "tier", "VARCHAR(16)"),
    ("orders", "followups_used", "INTEGER DEFAULT 0 NOT NULL"),
    ("users", "daily_subscribed", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("users", "birth_date", "DATE"),
    ("users", "birth_time", "VARCHAR(5)"),
    ("users", "birth_city", "VARCHAR(64)"),
    ("users", "city", "VARCHAR(64)"),
    ("users", "birth_lat", "FLOAT"),
    ("users", "birth_lon", "FLOAT"),
    ("users", "birth_tz", "VARCHAR(48)"),
    # Арендатор: единица — бот, с которого всё начиналось
    ("readings", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("orders", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("events", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("daily_cards", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("payments", "tenant_id", "INTEGER DEFAULT 1 NOT NULL"),
    ("payments", "kind", "VARCHAR(32) DEFAULT 'auto_reading' NOT NULL"),
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


# Чем заполнить обязательную колонку, которой в старой таблице не было.
# Схема росла постепенно, и строка пятилетней давности может не знать о поле,
# появившемся позже; без подстановки и ALTER, и перелив упали бы на NOT NULL.
FILLERS: dict[str, str] = {
    "DATETIME": "CURRENT_TIMESTAMP",
    "DATE": "CURRENT_DATE",
    "INTEGER": "0",
    "BIGINT": "0",
    "FLOAT": "0",
    "BOOLEAN": "0",
    "JSON": "'[]'",
}


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


# То же для ALTER TABLE, но только постоянными значениями: SQLite не во всех
# версиях принимает CURRENT_TIMESTAMP в добавляемой колонке. У даты рождения
# строки и у времени создания постоянной подстановки нет — и выдумывать её
# нечестно: такая колонка добавляется необязательной, и у старых строк пусто.
CONSTANTS: dict[str, str] = {
    kind: value for kind, value in FILLERS.items() if not value.startswith("CURRENT_")
}


def _literal(value: object) -> str:
    """Значение из модели в виде, пригодном для SQL."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _column_ddl(column, dialect) -> str:
    """Описание колонки для ALTER TABLE: тип, обязательность и чем заполнить.

    SQLite не добавит обязательную колонку без значения по умолчанию: он не
    знает, что писать в уже существующие строки. Берём то, что названо в
    модели, иначе — пустое значение по типу.
    """
    kind = column.type.compile(dialect)
    if column.nullable:
        return kind

    default = column.default
    filler = (
        _literal(default.arg)
        if default is not None and getattr(default, "is_scalar", False)
        else CONSTANTS.get(kind.split("(")[0].upper())
    )
    if filler is None:
        # Бот, который не поднимается, хуже колонки, где у старых строк пусто
        logger.warning("Колонку %s заполнить нечем — добавляю необязательной", column.name)
        return kind
    return f"{kind} DEFAULT {filler} NOT NULL"


def _add_from_model(conn) -> None:
    """Дописывает всё, что есть в модели и чего нет в таблице.

    Это второй проход, и он важнее первого: без него каждое новое поле молча
    ломает работающего бота, и узнаётся это трейсом с сервера.
    """
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for name, model in Base.metadata.tables.items():
        if name not in tables:
            continue  # create_all создаст её целиком
        existing = {column["name"] for column in inspector.get_columns(name)}
        if name in REBUILT_FOR_TENANT and "tenant_id" not in existing:
            # Такую таблицу сейчас пересоберут целиком, и колонка арендатора
            # должна войти в ключ. Добавить её здесь — значит отменить пересборку
            continue
        for column in model.columns:
            if column.name in existing:
                continue
            logger.info("Добавляю колонку %s.%s по модели", name, column.name)
            conn.execute(
                text(
                    f"ALTER TABLE {name} ADD COLUMN {column.name} "
                    f"{_column_ddl(column, conn.dialect)}"
                )
            )


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
        await conn.run_sync(_add_from_model)
        parked = await conn.run_sync(_park_tables_without_tenant_key)
        await conn.run_sync(Base.metadata.create_all)
        if parked:
            await conn.run_sync(_refill_rebuilt_tables, parked)
