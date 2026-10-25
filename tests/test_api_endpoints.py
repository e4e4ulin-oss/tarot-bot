"""Эндпоинты мини-приложения: что отдаём своим и чего не отдаём чужим."""

from __future__ import annotations

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from bot.api.app import create_app
from bot.config import Settings
from bot.db import create_engine, create_session_factory, init_models
from tests.test_api_auth import make_init_data

TOKEN = "test:token"
OTHER = "999:not-ours"


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/api.db")
    await init_models(engine)
    factory = create_session_factory(engine)
    settings = Settings()

    async def tokens() -> dict[int, str]:
        return {1: settings.bot_token}

    async def brands(tenant_id: int) -> Settings:
        return settings

    app = create_app(settings, factory, tokens, brands)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()
    await engine.dispose()


def auth(token: str = TOKEN, **kwargs) -> dict[str, str]:
    return {"Authorization": f"tma {make_init_data(token, **kwargs)}"}


@pytest.mark.asyncio
async def test_health_is_open(client):
    res = await client.get("/api/health")
    assert res.status == 200
    assert (await res.json())["ok"] is True


@pytest.mark.asyncio
async def test_config_is_open_and_lists_spreads(client):
    res = await client.get("/api/config")
    assert res.status == 200
    data = await res.json()
    # Витрину видно до входа: иначе на первом экране нечего показывать
    assert [item["key"] for item in data["spreads"]][0] == "day"
    assert data["free_readings"] >= 0


@pytest.mark.asyncio
async def test_me_requires_signature(client):
    res = await client.get("/api/me")
    assert res.status == 401


@pytest.mark.asyncio
async def test_me_rejects_foreign_signature(client):
    res = await client.get("/api/me", headers=auth(OTHER))
    assert res.status == 401


@pytest.mark.asyncio
async def test_me_returns_profile_for_signed_user(client):
    res = await client.get("/api/me", headers=auth(user_id=777))
    assert res.status == 200
    data = await res.json()
    assert data["user"]["id"] == 777
    assert data["readings"]["free_left"] == data["readings"]["free_limit"]
    assert data["history"] == []


@pytest.mark.asyncio
async def test_daily_is_empty_until_drawn(client):
    res = await client.get("/api/daily", headers=auth(user_id=5))
    assert res.status == 200
    assert (await res.json())["card"] is None


@pytest.mark.asyncio
async def test_daily_draw_returns_card_and_repeats_it(client):
    first = await (await client.post("/api/daily", headers=auth(user_id=5))).json()
    assert first["card"]["id"]
    assert first["card"]["image"].startswith("cards/")

    # Вторая попытка за те же сутки обязана вернуть ту же карту,
    # иначе человек будет тянуть, пока не выпадет приятная
    second = await (await client.post("/api/daily", headers=auth(user_id=5))).json()
    assert second["card"]["id"] == first["card"]["id"]

    shown = await (await client.get("/api/daily", headers=auth(user_id=5))).json()
    assert shown["card"]["id"] == first["card"]["id"]


@pytest.mark.asyncio
async def test_daily_cards_differ_between_people(client):
    mine = await (await client.post("/api/daily", headers=auth(user_id=11))).json()
    theirs = await (await client.get("/api/daily", headers=auth(user_id=12))).json()
    # У соседа своя карта, и до собственной тяги её нет
    assert theirs["card"] is None
    assert mine["card"] is not None


@pytest.mark.asyncio
async def test_subscription_toggles(client):
    on = await (await client.post("/api/daily/subscription", headers=auth(user_id=9))).json()
    assert on["subscribed"] is True
    off = await (await client.post("/api/daily/subscription", headers=auth(user_id=9))).json()
    assert off["subscribed"] is False
