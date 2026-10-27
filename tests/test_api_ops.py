"""Служебный адрес: присмотр за сервером снаружи, только на чтение.

Секрет здесь — единственная дверь, поэтому проверяется в первую очередь он:
без секрета адрес не должен даже признаваться, что существует.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from bot.api.app import create_app
from bot.config import Settings
from bot.db import create_engine, create_session_factory, init_models
from bot.db.models import Tenant
from bot.services.server import CommandResult

SECRET = "ops-секрет-достаточной-длины"


class FakeOps:
    """Вместо systemd и journalctl — заранее известные ответы."""

    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.asked_lines: int | None = None

    async def version(self, directory: str = "/opt/tarot-bot") -> CommandResult:
        if self.broken:
            return CommandResult(127, "git: not found")
        return CommandResult(0, "b25c9ef 2026-10-27T11:36:35+00:00 Сообщение коммита\n")

    async def state(self) -> CommandResult:
        if self.broken:
            return CommandResult(1, "Failed to connect to bus")
        return CommandResult(
            0, "ActiveState=active\nActiveEnterTimestamp=Mon 2026-10-27 11:40:02 UTC\n"
        )

    async def logs(self, lines: int = 40) -> CommandResult:
        self.asked_lines = lines
        return CommandResult(0, "строка журнала\nещё строка\n")


async def make_client(tmp_path, token: str = SECRET, broken: bool = False):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/ops.db")
    await init_models(engine)
    factory = create_session_factory(engine)
    settings = Settings(OPS_TOKEN=token)

    # Цены живут у арендатора, а не в коде, поэтому арендатор здесь нужен
    async with factory() as session:
        session.add(
            Tenant(
                slug="default",
                title="Таро",
                bot_token="123:abc",
                bot_id=123,
                auto_reading_stars=50,
                natal_reading_stars=150,
                payment_provider_token="1:LIVE:секрет",
            )
        )
        await session.commit()

    async def tokens() -> dict[int, str]:
        return {1: settings.bot_token}

    async def brands(tenant_id: int) -> Settings:
        return settings

    ops = FakeOps(broken=broken)
    app = create_app(settings, factory, tokens, brands, ops=ops)
    client = TestClient(TestServer(app))
    await client.start_server()
    client.ops = ops
    client.engine = engine
    return client


@pytest_asyncio.fixture
async def client(tmp_path):
    client = await make_client(tmp_path)
    yield client
    await client.close()
    await client.engine.dispose()


def key(value: str = SECRET) -> dict[str, str]:
    return {"X-Ops-Token": value}


@pytest.mark.asyncio
async def test_without_the_secret_the_address_does_not_exist(client):
    """Не «нельзя», а «нет такого»: о существовании двери знать незачем."""
    for headers in ({}, key("не тот секрет")):
        res = await client.get("/api/ops/status", headers=headers)
        assert res.status == 404
        assert (await res.json())["error"] == "not_found"


@pytest.mark.asyncio
async def test_a_bearer_header_works_too(client):
    """Хранилища секретов обычно умеют Bearer — пусть он и подойдёт."""
    res = await client.get(
        "/api/ops/status", headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert res.status == 200

    wrong = await client.get("/api/ops/status", headers={"Authorization": "Bearer чужой"})
    assert wrong.status == 404


@pytest.mark.asyncio
async def test_the_status_says_what_is_deployed_and_alive(client):
    res = await client.get("/api/ops/status", headers=key())
    assert res.status == 200

    data = await res.json()
    assert data["version"].startswith("b25c9ef")
    assert data["service"]["ActiveState"] == "active"
    # База тоже отвечает — иначе цифр бы не было
    assert "users" in data["counts"]


@pytest.mark.asyncio
async def test_the_status_shows_what_is_actually_being_sold(client):
    """Цена лежит в базе: по одной версии кода не узнать, почём сейчас продают."""
    res = await client.get("/api/ops/status", headers=key())
    brand = (await res.json())["tenants"][0]

    assert brand["slug"] == "default"
    assert brand["payment_method"] == "stars"
    assert brand["stars"] == {"auto": 50, "natal": 150}
    assert brand["free"]["per_day"] == 2


@pytest.mark.asyncio
async def test_the_status_tells_about_the_provider_without_telling_its_token(client):
    res = await client.get("/api/ops/status", headers=key())
    body = await res.text()

    assert (await res.json())["tenants"][0]["provider_ready"] is True
    # Признак — да, сам токен — никогда
    assert "1:LIVE" not in body
    assert "123:abc" not in body


@pytest.mark.asyncio
async def test_the_journal_comes_through(client):
    res = await client.get("/api/ops/logs?lines=15", headers=key())
    assert res.status == 200

    data = await res.json()
    assert "строка журнала" in data["text"]
    assert client.ops.asked_lines == 15


@pytest.mark.asyncio
async def test_a_broken_server_is_described_not_hidden(tmp_path):
    """Если systemd недоступен, это и должно быть видно в ответе."""
    client = await make_client(tmp_path, broken=True)
    data = await (await client.get("/api/ops/status", headers=key())).json()

    assert data["version"] is None and "git" in data["version_error"]
    assert "error" in data["service"]

    await client.close()
    await client.engine.dispose()


@pytest.mark.asyncio
async def test_with_no_token_in_settings_the_address_is_off(tmp_path):
    """Выключен по умолчанию: секрет не задан — адреса нет."""
    client = await make_client(tmp_path, token="")
    assert (await client.get("/api/ops/status", headers=key())).status == 404

    await client.close()
    await client.engine.dispose()


@pytest.mark.asyncio
async def test_nothing_here_changes_anything(client):
    """Присмотр — для глаз, а не для рук: ни обновления, ни перезапуска."""
    import inspect

    from bot.api import app as module

    source = inspect.getsource(module)
    ops_part = source[source.index("def ops_allowed") : source.index("async def handle_admin_orders")]
    for forbidden in ("update(", "restart(", "add_post"):
        assert forbidden not in ops_part, forbidden
