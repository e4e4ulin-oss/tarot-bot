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


async def subscribe(client, user_id: int) -> None:
    """Бесплатные расклады положены подписчикам утренней карты — это правило бота."""
    await client.post("/api/daily/subscription", headers=auth(user_id=user_id))


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


@pytest.mark.asyncio
async def test_reading_start_returns_cards_for_the_spread(client):
    await subscribe(client, 21)
    res = await client.post(
        "/api/reading",
        headers=auth(user_id=21),
        json={"spread": "ptf", "question": "что с работой", "number": 7},
    )
    assert res.status == 200
    data = await res.json()
    assert data["allowed"] is True
    assert len(data["cards"]) == 3
    # Позиции приходят вместе с картами: без них расклад не прочитать
    assert [card["position"] for card in data["cards"]] == [
        "Прошлое",
        "Настоящее",
        "Будущее",
    ]
    assert data["token"]


@pytest.mark.asyncio
async def test_reading_is_repeatable_for_the_same_number(client):
    await subscribe(client, 22)
    body = {"spread": "sit", "question": "одинаково ли", "number": 13}
    first = await (await client.post("/api/reading", headers=auth(user_id=22), json=body)).json()
    second = await (await client.post("/api/reading", headers=auth(user_id=22), json=body)).json()
    # Карты ложатся под названное число, а не под случайность сервера
    assert [c["id"] for c in first["cards"]] == [c["id"] for c in second["cards"]]


@pytest.mark.asyncio
async def test_reading_finish_writes_interpretation_and_history(client):
    await subscribe(client, 23)
    started = await (
        await client.post(
            "/api/reading", headers=auth(user_id=23), json={"spread": "day", "number": 3}
        )
    ).json()
    res = await client.post(
        f"/api/reading/{started['token']}/interpretation", headers=auth(user_id=23)
    )
    assert res.status == 200
    done = await res.json()
    assert done["text"]
    # Бесплатных стало меньше ровно на этот расклад
    assert done["free_left"] == 2

    me = await (await client.get("/api/me", headers=auth(user_id=23))).json()
    assert len(me["history"]) == 1
    assert me["history"][0]["spread"] == "day"


@pytest.mark.asyncio
async def test_interpretation_of_a_foreign_token_is_refused(client):
    await subscribe(client, 24)
    started = await (
        await client.post(
            "/api/reading", headers=auth(user_id=24), json={"spread": "day", "number": 1}
        )
    ).json()
    res = await client.post(
        f"/api/reading/{started['token']}/interpretation", headers=auth(user_id=25)
    )
    assert res.status == 403


@pytest.mark.asyncio
async def test_unknown_token_is_expired(client):
    res = await client.post("/api/reading/nothing/interpretation", headers=auth(user_id=26))
    assert res.status == 404


@pytest.mark.asyncio
async def test_yes_no_spread_demands_a_question(client):
    res = await client.post(
        "/api/reading", headers=auth(user_id=27), json={"spread": "yes_no", "number": 5}
    )
    assert res.status == 400


@pytest.mark.asyncio
async def test_free_readings_run_out(client):
    await subscribe(client, 28)
    for _ in range(3):
        started = await (
            await client.post(
                "/api/reading", headers=auth(user_id=28), json={"spread": "day", "number": 2}
            )
        ).json()
        assert started["allowed"] is True
        await client.post(
            f"/api/reading/{started['token']}/interpretation", headers=auth(user_id=28)
        )

    refused = await (
        await client.post(
            "/api/reading", headers=auth(user_id=28), json={"spread": "day", "number": 2}
        )
    ).json()
    # Отказ приходит обычным ответом: приложению надо показать, чего не хватает
    assert refused["allowed"] is False
    assert refused["reason"] in ("needs_payment", "payment_not_ready", "needs_daily")
