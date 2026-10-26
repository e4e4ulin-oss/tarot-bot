"""Эндпоинты мини-приложения: что отдаём своим и чего не отдаём чужим."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from bot.api.app import SESSIONS, create_app
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


@pytest.mark.asyncio
async def test_city_is_found_by_name(client):
    res = await client.get("/api/cities?q=Саратов", headers=auth(user_id=31))
    assert res.status == 200
    cities = (await res.json())["cities"]
    # Справочник ищет по названию целиком и отвечает латиницей,
    # как его и собрали: нам важны координаты и пояс, а не написание
    assert len(cities) == 1
    assert cities[0]["name"] == "Saratov"
    assert cities[0]["id"]


@pytest.mark.asyncio
async def test_namesakes_come_back_together(client):
    res = await client.get("/api/cities?q=Москва", headers=auth(user_id=31))
    cities = (await res.json())["cities"]
    # Тёзок двое — выбор остаётся за человеком, сами не угадываем
    assert len(cities) > 1


@pytest.mark.asyncio
async def test_short_query_suggests_nothing(client):
    res = await client.get("/api/cities?q=с", headers=auth(user_id=31))
    assert (await res.json())["cities"] == []


@pytest.mark.asyncio
async def test_profile_saves_birth_data_and_locates_city(client):
    res = await client.put(
        "/api/profile",
        headers=auth(user_id=32),
        json={"birth_date": "25.07.1992", "birth_time": "14:30", "birth_city": "Саратов"},
    )
    assert res.status == 200
    data = await res.json()
    assert data["birth"]["date"] == "1992-07-25"
    assert data["birth"]["time"] == "14:30"
    # Координаты нашлись — значит асцендент посчитается
    assert data["birth"]["located"] is True


@pytest.mark.asyncio
async def test_profile_refuses_nonsense_date(client):
    res = await client.put(
        "/api/profile", headers=auth(user_id=33), json={"birth_date": "тридцатое"}
    )
    assert res.status == 400


@pytest.mark.asyncio
async def test_chart_waits_for_the_birth_date(client):
    res = await client.get("/api/chart", headers=auth(user_id=34))
    assert (await res.json())["ready"] is False


@pytest.mark.asyncio
async def test_chart_is_built_once_the_date_is_known(client):
    await client.put(
        "/api/profile",
        headers=auth(user_id=35),
        json={"birth_date": "25.07.1992", "birth_time": "14:30", "birth_city": "Саратов"},
    )
    data = await (await client.get("/api/chart", headers=auth(user_id=35))).json()
    assert data["ready"] is True
    assert any("Солнце" in line for line in data["lines"])
    # Время и город известны — дома считаются
    assert data["has_houses"] is True


@pytest.mark.asyncio
async def test_forecast_follows_the_chart(client):
    empty = await (await client.get("/api/forecast", headers=auth(user_id=36))).json()
    assert empty["ready"] is False

    await client.put("/api/profile", headers=auth(user_id=36), json={"birth_date": "25.07.1992"})
    data = await (await client.get("/api/forecast", headers=auth(user_id=36))).json()
    assert data["ready"] is True
    assert isinstance(data["transits"], list)


@pytest.mark.asyncio
async def test_order_is_created_and_blocks_a_second_one(client):
    res = await client.post(
        "/api/order",
        headers=auth(user_id=41),
        json={"tier": "short", "question": "что делать с переездом", "contact": "@diana"},
    )
    assert res.status == 200
    first = await res.json()
    assert first["created"] is True
    assert first["order"]["id"]

    again = await (
        await client.post(
            "/api/order", headers=auth(user_id=41), json={"question": "ещё один вопрос"}
        )
    ).json()
    # Вторую заявку не принимаем, пока первая в работе
    assert again["created"] is False
    assert again["reason"] == "already_open"
    assert again["order"]["id"] == first["order"]["id"]


@pytest.mark.asyncio
async def test_order_needs_a_question(client):
    res = await client.post("/api/order", headers=auth(user_id=42), json={"tier": "short"})
    assert res.status == 400


@pytest.mark.asyncio
async def test_unknown_tier_is_refused(client):
    res = await client.post(
        "/api/order", headers=auth(user_id=43), json={"question": "вопрос", "tier": "royal"}
    )
    assert res.status == 400


@pytest.mark.asyncio
async def test_order_can_be_cancelled_while_untouched(client):
    created = await (
        await client.post("/api/order", headers=auth(user_id=44), json={"question": "вопрос"})
    ).json()
    order_id = created["order"]["id"]

    res = await client.delete(f"/api/order/{order_id}", headers=auth(user_id=44))
    assert res.status == 200

    # Отменённая заявка больше не держит место: можно подать новую
    second = await (
        await client.post("/api/order", headers=auth(user_id=44), json={"question": "другой"})
    ).json()
    assert second["created"] is True


@pytest.mark.asyncio
async def test_foreign_order_cannot_be_cancelled(client):
    created = await (
        await client.post("/api/order", headers=auth(user_id=45), json={"question": "вопрос"})
    ).json()
    res = await client.delete(f"/api/order/{created['order']['id']}", headers=auth(user_id=46))
    assert res.status == 404


@pytest.mark.asyncio
async def test_invoice_says_when_payments_are_not_set_up(client):
    res = await client.post("/api/invoice", headers=auth(user_id=47), json={"kind": "auto"})
    # Токена продавца в тестовых настройках нет — и приложение должно это объяснить
    assert res.status == 409
    assert (await res.json())["error"] == "payment_not_ready"


class FakeBot:
    """Бот нужен тут для двух вещей: назвать имя и донести сообщение.

    Имя идёт в ссылку-приглашение, сообщения — тарологу: по ним и видно,
    что уточнение из приложения доходит туда же, куда из переписки.
    """

    class Me:
        username = "Taroautorasklad_bot"
        id = 1

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def me(self) -> Me:
        return self.Me()

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return None


@pytest_asyncio.fixture
async def client_with_bot(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/invite.db")
    await init_models(engine)
    factory = create_session_factory(engine)
    settings = Settings()

    async def tokens() -> dict[int, str]:
        return {1: settings.bot_token}

    async def brands(tenant_id: int) -> Settings:
        return settings

    robot = FakeBot()

    async def bots(tenant_id: int) -> FakeBot:
        return robot

    app = create_app(settings, factory, tokens, brands, bots=bots)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    # Фабрика сессий нужна и самому тесту: промокод заводит владелец, а не
    # приложение — в базу его кладём мимо API
    test_client.sessions = factory
    test_client.robot = robot
    yield test_client
    await test_client.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_invite_screen_has_a_link_and_a_count(client_with_bot):
    res = await client_with_bot.get("/api/invite", headers=auth(user_id=4242))
    assert res.status == 200

    data = await res.json()
    assert data["link"].endswith("?start=ref4242")
    assert data["invited"] == 0
    assert data["bonus"] >= 1 and data["welcome"] >= 0


@pytest.mark.asyncio
async def test_a_promo_code_adds_readings_in_the_app(client_with_bot):
    # Код заводит владелец; приложению остаётся его принять
    from bot.db import Repo

    async with client_with_bot.sessions() as session:
        await Repo(session).add_promo("ОСЕНЬ", readings=4)

    res = await client_with_bot.post(
        "/api/promo", headers=auth(user_id=11), data='{"code": "осень"}'
    )
    assert res.status == 200
    data = await res.json()
    assert data["readings"] == 4 and data["bonus"] == 4

    # Второй раз тем же человеком — отказ со своим словом, а не «ошибка»
    again = await client_with_bot.post(
        "/api/promo", headers=auth(user_id=11), data='{"code": "ОСЕНЬ"}'
    )
    assert again.status == 409
    assert (await again.json())["error"] == "already"


@pytest.mark.asyncio
async def test_the_cabinet_counts_the_earned_readings(client_with_bot):
    """Остаток в кабинете обязан знать про расклады за друзей и коды."""
    from bot.db import Repo

    async with client_with_bot.sessions() as session:
        repo = Repo(session)
        await repo.upsert_user(55, None, "Клиент")
        await repo.set_daily_subscription(55, True)
        await repo.add_bonus(55, 2)

    data = await (await client_with_bot.get("/api/me", headers=auth(user_id=55))).json()

    assert data["readings"]["bonus"] == 2
    assert data["readings"]["free_limit"] == data["readings"]["free_left"] >= 2


# --- Хозяйский экран промокодов ------------------------------------------
OWNER = 1  # он же в ADMIN_IDS, см. tests/conftest.py
CLIENT = 77


@pytest.mark.asyncio
async def test_the_cabinet_says_who_is_the_owner(client):
    mine = await (await client.get("/api/me", headers=auth(user_id=OWNER))).json()
    theirs = await (await client.get("/api/me", headers=auth(user_id=CLIENT))).json()

    assert mine["admin"] is True
    assert theirs["admin"] is False


@pytest.mark.asyncio
async def test_a_client_cannot_see_or_make_codes(client):
    """Спрятать кнопку мало: проверяет сервер, а не вёрстка."""
    listing = await client.get("/api/admin/promos", headers=auth(user_id=CLIENT))
    assert listing.status == 403

    made = await client.post(
        "/api/admin/promos",
        headers=auth(user_id=CLIENT),
        data='{"code": "ЧУЖОЙ", "readings": 50}',
    )
    assert made.status == 403

    # И список остался пустым: отказ был отказом, а не тихим созданием
    owner_sees = await (await client.get("/api/admin/promos", headers=auth(user_id=OWNER))).json()
    assert owner_sees["codes"] == []


@pytest.mark.asyncio
async def test_the_owner_creates_a_code_and_sees_it_in_the_list(client):
    made = await client.post(
        "/api/admin/promos",
        headers=auth(user_id=OWNER),
        data='{"code": "осень", "readings": 3, "uses_limit": 10, "expires_at": "2027-01-31"}',
    )
    assert made.status == 201
    created = await made.json()
    assert created["code"] == "ОСЕНЬ" and created["readings"] == 3
    assert created["used"] == 0 and created["active"] is True

    listing = await (await client.get("/api/admin/promos", headers=auth(user_id=OWNER))).json()
    assert [item["code"] for item in listing["codes"]] == ["ОСЕНЬ"]

    # Второй раз тем же словом — отказ, а не второй код
    again = await client.post(
        "/api/admin/promos", headers=auth(user_id=OWNER), data='{"code": "ОСЕНЬ", "readings": 1}'
    )
    assert again.status == 409


@pytest.mark.asyncio
async def test_a_slip_of_the_finger_does_not_become_a_gift(client):
    """Щедрость с опечаткой не отменить: сто раскладов уже у человека."""
    too_much = await client.post(
        "/api/admin/promos", headers=auth(user_id=OWNER), data='{"code": "ОЙ", "readings": 5000}'
    )
    assert too_much.status == 400 and (await too_much.json())["error"] == "readings"

    nothing = await client.post(
        "/api/admin/promos", headers=auth(user_id=OWNER), data='{"code": "  ", "readings": 2}'
    )
    assert nothing.status == 400 and (await nothing.json())["error"] == "empty"

    bad_date = await client.post(
        "/api/admin/promos",
        headers=auth(user_id=OWNER),
        data='{"code": "КОГДА", "readings": 2, "expires_at": "31.01.2027"}',
    )
    assert bad_date.status == 400 and (await bad_date.json())["error"] == "expires_at"


@pytest.mark.asyncio
async def test_switching_a_code_off_stops_it_working(client):
    made = await (
        await client.post(
            "/api/admin/promos",
            headers=auth(user_id=OWNER),
            data='{"code": "ПАУЗА", "readings": 2}',
        )
    ).json()

    off = await client.post(
        f"/api/admin/promos/{made['id']}/active",
        headers=auth(user_id=OWNER),
        data='{"active": false}',
    )
    assert off.status == 200 and (await off.json())["active"] is False

    refused = await client.post(
        "/api/promo", headers=auth(user_id=CLIENT), data='{"code": "ПАУЗА"}'
    )
    assert refused.status == 409 and (await refused.json())["error"] == "disabled"

    # И обратно: код живёт дальше, его не удаляли
    on = await client.post(
        f"/api/admin/promos/{made['id']}/active",
        headers=auth(user_id=OWNER),
        data='{"active": true}',
    )
    assert (await on.json())["active"] is True

    works = await client.post("/api/promo", headers=auth(user_id=CLIENT), data='{"code": "ПАУЗА"}')
    assert works.status == 200


@pytest.mark.asyncio
async def test_a_past_reading_opens_again(client):
    """Разбор пишется один раз — перечитать его человек вправе."""
    from bot.db import Repo
    from bot.tarot import draw_spread, get_spread, serialize

    drawn = draw_spread(get_spread("ptf"))
    async with client.server.app[SESSIONS]() as session:
        repo = Repo(session)
        await repo.upsert_user(321, None, "Клиент")
        saved = await repo.save_reading(
            user_id=321,
            spread_key="ptf",
            question="про работу",
            cards=serialize(drawn),
            interpretation="Тот самый разбор.",
            ai_used=True,
        )

    res = await client.get(f"/api/readings/{saved.id}", headers=auth(user_id=321))
    assert res.status == 200

    data = await res.json()
    assert data["title"] == "Прошлое — Настоящее — Будущее"
    assert data["question"] == "про работу"
    assert data["text"] == "Тот самый разбор."
    assert len(data["cards"]) == 3
    # Карты те же и в том же порядке, с картинкой и позицией
    assert [card["id"] for card in data["cards"]] == [item.card.id for item in drawn]
    assert data["cards"][0]["image"].startswith("cards/")
    assert data["cards"][0]["position"] == "Прошлое"


@pytest.mark.asyncio
async def test_someone_elses_reading_is_simply_not_found(client):
    """Чужой и несуществующий отвечают одинаково: иначе чужие пересчитываются."""
    from bot.db import Repo

    async with client.server.app[SESSIONS]() as session:
        repo = Repo(session)
        await repo.upsert_user(401, None, "Хозяин")
        mine = await repo.save_reading(
            user_id=401, spread_key="day", question=None, cards=[],
            interpretation="Личное.", ai_used=False,
        )

    stranger = await client.get(f"/api/readings/{mine.id}", headers=auth(user_id=402))
    missing = await client.get("/api/readings/999999", headers=auth(user_id=402))
    nonsense = await client.get("/api/readings/абв", headers=auth(user_id=402))

    assert stranger.status == missing.status == nonsense.status == 404
    assert (await stranger.json()) == (await missing.json())


# --- Авторский разбор у клиента ------------------------------------------


@pytest.mark.asyncio
async def test_the_answer_is_readable_in_the_app(client_with_bot):
    """За ответ платят — значит перечитывать его надо там, где платили."""
    from bot.db import Repo
    from bot.db.models import OrderStatus

    async with client_with_bot.sessions() as session:
        repo = Repo(session)
        await repo.upsert_user(900, None, "Клиент")
        order = await repo.create_order(
            user_id=900, question="Что с работой?", topic=None, contact=None, tier="base"
        )
        await repo.add_order_message(order.id, "author", "Вот ваш разбор.")
        await repo.set_order_status(order, OrderStatus.ANSWERED, admin_id=1)

    listing = await (await client_with_bot.get("/api/orders", headers=auth(user_id=900))).json()
    assert [item["id"] for item in listing["orders"]] == [order.id]
    assert listing["orders"][0]["label"] == "Отвечена"

    one = await (
        await client_with_bot.get(f"/api/orders/{order.id}", headers=auth(user_id=900))
    ).json()
    assert one["question"] == "Что с работой?"
    assert [(m["author"], m["text"]) for m in one["thread"]] == [("author", "Вот ваш разбор.")]
    assert one["followups"]["left"] >= 1


@pytest.mark.asyncio
async def test_a_followup_reaches_the_author_and_counts(client_with_bot):
    from bot.db import Repo
    from bot.db.models import OrderStatus

    async with client_with_bot.sessions() as session:
        repo = Repo(session)
        await repo.upsert_user(901, None, "Клиент")
        order = await repo.create_order(
            user_id=901, question="Про отношения", topic=None, contact=None, tier="base"
        )
        await repo.add_order_message(order.id, "author", "Ответ.")
        await repo.set_order_status(order, OrderStatus.ANSWERED, admin_id=1)

    sent = await client_with_bot.post(
        f"/api/orders/{order.id}/followup",
        headers=auth(user_id=901),
        data=json.dumps({"text": "А если подождать?"}),
    )
    assert sent.status == 200
    assert (await sent.json())["followups"]["left"] == 0  # в «базовом» уточнение одно

    # Таролог узнал об уточнении — тем же сообщением, что и из переписки
    assert any("А если подождать?" in said for _, said in client_with_bot.robot.sent)

    thread = await (
        await client_with_bot.get(f"/api/orders/{order.id}", headers=auth(user_id=901))
    ).json()
    assert [m["author"] for m in thread["thread"]] == ["author", "client"]

    # Второе уточнение по тому же уровню не проходит
    again = await client_with_bot.post(
        f"/api/orders/{order.id}/followup",
        headers=auth(user_id=901),
        data=json.dumps({"text": "ещё вопрос"}),
    )
    assert again.status == 409 and (await again.json())["error"] == "spent"


@pytest.mark.asyncio
async def test_a_followup_waits_for_the_answer(client_with_bot):
    """Уточнять нечего, пока разбора нет."""
    from bot.db import Repo

    async with client_with_bot.sessions() as session:
        repo = Repo(session)
        await repo.upsert_user(902, None, "Клиент")
        order = await repo.create_order(
            user_id=902, question="Вопрос", topic=None, contact=None, tier="full"
        )

    early = await client_with_bot.post(
        f"/api/orders/{order.id}/followup",
        headers=auth(user_id=902),
        data=json.dumps({"text": "а когда?"}),
    )
    assert early.status == 409 and (await early.json())["error"] == "not_answered"


@pytest.mark.asyncio
async def test_someone_elses_order_is_not_found(client_with_bot):
    from bot.db import Repo

    async with client_with_bot.sessions() as session:
        repo = Repo(session)
        await repo.upsert_user(903, None, "Хозяин")
        order = await repo.create_order(
            user_id=903, question="Личное", topic=None, contact=None, tier="short"
        )

    peek = await client_with_bot.get(f"/api/orders/{order.id}", headers=auth(user_id=904))
    poke = await client_with_bot.post(
        f"/api/orders/{order.id}/followup",
        headers=auth(user_id=904),
        data=json.dumps({"text": "чужое"}),
    )
    assert peek.status == 404 and poke.status == 404
