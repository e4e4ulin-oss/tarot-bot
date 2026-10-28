"""Партия через приложение: кто может начать, что приходит и что не ломается.

Сама игра проверяется отдельно и без сети. Здесь — деньги, состояние между
запросами и поведение, когда модель молчит.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from bot import guess
from bot.api.app import create_app
from bot.config import Settings
from bot.db import Repo, create_engine, create_session_factory, init_models
from bot.services.grok import GrokError
from tests.test_api_auth import make_init_data

TOKEN = "test:token"
PLAYER = 501


def auth(user_id: int = PLAYER) -> dict[str, str]:
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=user_id)}


class FakeBot:
    """Возвраты не уходят в Telegram: записываем, что попросили вернуть."""

    def __init__(self, broken: bool = False) -> None:
        self.refunds: list[tuple[int, str]] = []
        self.broken = broken

    async def refund_star_payment(self, user_id: int, telegram_payment_charge_id: str):
        if self.broken:
            raise RuntimeError("Telegram отказал")
        self.refunds.append((user_id, telegram_payment_charge_id))
        return True


class FakeGuessing:
    """Выдаёт заготовленные ходы по очереди и считает обращения."""

    def __init__(self, *turns, broken: bool = False) -> None:
        self.queue = list(turns)
        self.broken = broken
        self.calls = 0
        self.enabled = True

    async def step(self, game, **kwargs):
        self.calls += 1
        if self.broken:
            raise GrokError("модель молчит")
        if self.queue:
            return self.queue.pop(0)
        return question_turn(f"Вопрос {self.calls}?")


def question_turn(text="Этот человек жив?"):
    return guess.Turn(
        question=text,
        card="major_19",
        card_answer=guess.CARD_YES,
        reading="Солнце светит — да",
    )


def lead_turn(*names):
    return guess.Turn(names=list(names), card="major_17", reading="Карты показывают типаж")


@pytest_asyncio.fixture
async def make_client(tmp_path):
    created = []

    async def build(guessing, *, profile: bool = True, robot=None, **extra):
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/guess{len(created)}.db")
        await init_models(engine)
        factory = create_session_factory(engine)
        extra.setdefault("GUESS_ENABLED", True)
        settings = Settings(**extra)

        async def tokens() -> dict[int, str]:
            return {1: settings.bot_token}

        async def brands(tenant_id: int) -> Settings:
            return settings

        if profile:
            # Бесплатная партия даётся в обмен на данные: без них не начать
            async with factory() as session:
                repo = Repo(session)
                await repo.upsert_user(PLAYER, None, "Игрок")
                await repo.save_profile(PLAYER, birth_date=date(1975, 6, 1), city="Москва")

        robot_for_app = robot or FakeBot()

        async def bots(tenant_id: int):
            return robot_for_app

        app = create_app(
            settings, factory, tokens, brands, bots=bots, guessing=guessing
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        client.sessions = factory
        client.robot = robot_for_app
        created.append((client, engine))
        return client

    yield build
    for client, engine in created:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_first_party_is_free_and_starts_with_a_question(make_client):
    client = await make_client(FakeGuessing(question_turn("Этот человек жив?")))

    before = await (await client.get("/api/guess", headers=auth())).json()
    assert before["game"] is None
    assert before["offer"]["can_start"] is True
    assert before["offer"]["price"] == 100

    data = await (await client.post("/api/guess", headers=auth())).json()
    turn = data["game"]["turn"]
    assert turn["kind"] == "question"
    assert turn["question"] == "Этот человек жив?"
    assert turn["card"]["id"] == "major_19"
    assert turn["options"] == list(guess.VERDICTS)


@pytest.mark.asyncio
async def test_a_second_party_has_to_be_paid_for(make_client):
    client = await make_client(FakeGuessing())
    await client.post("/api/guess", headers=auth())

    # Доигрываем первую до конца, чтобы она перестала быть активной
    async with client.sessions() as session:
        repo = Repo(session)
        row = await repo.active_game(PLAYER)
        await repo.save_game(row, row.state, guess.WON)

    res = await client.post("/api/guess", headers=auth())
    assert res.status == 402
    assert (await res.json())["error"] == "needs_payment"

    state = await (await client.get("/api/guess", headers=auth())).json()
    assert state["offer"]["can_start"] is False
    assert state["offer"]["free_left"] == 0


@pytest.mark.asyncio
async def test_a_paid_party_spends_the_payment_once(make_client):
    client = await make_client(FakeGuessing())
    await client.post("/api/guess", headers=auth())

    async with client.sessions() as session:
        repo = Repo(session)
        row = await repo.active_game(PLAYER)
        await repo.save_game(row, row.state, guess.WON)
        await repo.add_payment(
            user_id=PLAYER, amount=100, currency="XTR", charge_id="c1", kind="guess_game"
        )

    assert (await client.post("/api/guess", headers=auth())).status == 200

    async with client.sessions() as session:
        assert await Repo(session).unspent_payment(PLAYER, "guess_game") is None


@pytest.mark.asyncio
async def test_a_party_already_running_is_not_started_twice(make_client):
    """Вторая партия поверх первой — двойная плата ни за что."""
    guessing = FakeGuessing()
    client = await make_client(guessing)

    first = await (await client.post("/api/guess", headers=auth())).json()
    again = await (await client.post("/api/guess", headers=auth())).json()

    assert again["game"]["asked"] == first["game"]["asked"] == 1
    assert guessing.calls == 1  # второй ход у модели не просили


@pytest.mark.asyncio
async def test_an_answer_moves_the_party_on(make_client):
    client = await make_client(FakeGuessing(question_turn("Он жив?"), question_turn("Он поэт?")))
    await client.post("/api/guess", headers=auth())

    data = await (
        await client.post("/api/guess/answer", headers=auth(), json={"verdict": "no"})
    ).json()

    assert data["game"]["asked"] == 2
    assert data["game"]["turn"]["question"] == "Он поэт?"
    # Оценённый ход ушёл в историю, и правда в нём обратная ответу карты
    assert data["game"]["history"][0]["verdict"] == "no"


@pytest.mark.asyncio
async def test_a_wrong_verdict_is_refused_without_touching_the_party(make_client):
    client = await make_client(FakeGuessing())
    await client.post("/api/guess", headers=auth())

    res = await client.post("/api/guess/answer", headers=auth(), json={"verdict": "ага"})
    assert res.status == 400

    state = await (await client.get("/api/guess", headers=auth())).json()
    assert state["game"]["asked"] == 1
    assert state["game"]["turn"]["verdict"] is None


@pytest.mark.asyncio
async def test_a_name_guessed_right_ends_the_party(make_client):
    client = await make_client(FakeGuessing(lead_turn("Фредди Меркьюри")))
    await client.post("/api/guess", headers=auth())

    data = await (
        await client.post("/api/guess/answer", headers=auth(), json={"verdict": "yes"})
    ).json()

    assert data["game"]["status"] == guess.WON
    assert data["game"]["turn"] is None


@pytest.mark.asyncio
async def test_answering_without_a_party_is_a_plain_404(make_client):
    client = await make_client(FakeGuessing())
    res = await client.post("/api/guess/answer", headers=auth(), json={"verdict": "yes"})
    assert res.status == 404


@pytest.mark.asyncio
async def test_a_silent_model_keeps_the_verdict_that_was_given(make_client):
    """Оценка уже дана человеком — терять её из-за молчания модели нельзя."""
    guessing = FakeGuessing(question_turn())
    client = await make_client(guessing)
    await client.post("/api/guess", headers=auth())

    guessing.broken = True
    res = await client.post("/api/guess/answer", headers=auth(), json={"verdict": "yes"})
    assert res.status == 503

    state = await (await client.get("/api/guess", headers=auth())).json()
    assert state["game"]["history"][0]["verdict"] == "yes"
    assert state["game"]["turn"] is None  # ход не состоялся, но оценка цела


@pytest.mark.asyncio
async def test_a_party_that_never_started_costs_nothing(make_client):
    """Модель молчит — звёзды не списываются и партия не заводится."""
    client = await make_client(FakeGuessing(broken=True))

    res = await client.post("/api/guess", headers=auth())
    assert res.status == 503

    async with client.sessions() as session:
        assert await Repo(session).count_games(PLAYER) == 0


@pytest.mark.asyncio
async def test_the_secret_is_only_asked_after_a_loss(make_client):
    client = await make_client(FakeGuessing())
    await client.post("/api/guess", headers=auth())

    early = await client.post("/api/guess/secret", headers=auth(), json={"name": "Цой"})
    assert early.status == 404

    async with client.sessions() as session:
        repo = Repo(session)
        row = await repo.active_game(PLAYER)
        await repo.save_game(row, row.state, guess.LOST)

    told = await client.post("/api/guess/secret", headers=auth(), json={"name": "Цой"})
    assert told.status == 200
    assert (await told.json())["game"]["secret"] == "Цой"

    again = await client.post("/api/guess/secret", headers=auth(), json={"name": "Другой"})
    assert again.status == 409


@pytest.mark.asyncio
async def test_the_party_has_its_own_invoice(make_client):
    """Счёт за партию — отдельный товар: оплата расклада её не открывает."""
    from bot import billing

    await make_client(FakeGuessing())
    settings = Settings()
    deal = billing.terms(settings, billing.PAYLOAD_GUESS)

    assert deal.price == settings.guess_game_stars
    assert deal.payload == "guess_game"
    assert billing.terms(settings, billing.PAYLOAD_AUTO).payload != deal.payload


@pytest.mark.asyncio
async def test_the_free_party_waits_for_the_data_it_will_use(make_client):
    """Данные нужны подсказке о круге — за них и даётся бесплатная партия."""
    client = await make_client(FakeGuessing(), profile=False)

    offer = (await (await client.get("/api/guess", headers=auth())).json())["offer"]
    assert offer["can_start"] is False
    assert offer["needs_profile"] is True

    res = await client.post("/api/guess", headers=auth())
    assert res.status == 403
    assert (await res.json())["error"] == "needs_profile"

    async with client.sessions() as session:
        assert await Repo(session).count_games(PLAYER) == 0


@pytest.mark.asyncio
async def test_once_the_data_is_there_the_party_starts(make_client):
    client = await make_client(FakeGuessing())

    offer = (await (await client.get("/api/guess", headers=auth())).json())["offer"]
    assert offer["needs_profile"] is False
    assert offer["can_start"] is True
    assert (await client.post("/api/guess", headers=auth())).status == 200


@pytest.mark.asyncio
async def test_a_paid_party_needs_no_profile(make_client):
    client = await make_client(FakeGuessing(), profile=False)
    async with client.sessions() as session:
        repo = Repo(session)
        await repo.upsert_user(PLAYER, None, "Игрок")
        await repo.add_payment(
            user_id=PLAYER, amount=100, currency="XTR", charge_id="c9", kind="guess_game"
        )

    offer = (await (await client.get("/api/guess", headers=auth())).json())["offer"]
    assert offer["needs_profile"] is False
    assert offer["can_start"] is True
    assert (await client.post("/api/guess", headers=auth())).status == 200


# --- гарантия: не справились — вернули --------------------------------------


async def paid_party(client, guessing, *, turns_left=0):
    """Доводит человека до оплаченной партии и возвращает её строку."""
    async with client.sessions() as session:
        repo = Repo(session)
        row = await repo.start_game(PLAYER)  # первая, бесплатная
        await repo.save_game(row, {}, guess.WON)
        await repo.add_payment(
            user_id=PLAYER, amount=100, currency="XTR", charge_id="pay-1", kind="guess_game"
        )
    await client.post("/api/guess", headers=auth())
    async with client.sessions() as session:
        return await Repo(session).active_game(PLAYER)


async def lose_the_party(client):
    """Доводит активную партию до проигрыша: бюджет имён исчерпан."""
    async with client.sessions() as session:
        repo = Repo(session)
        row = await repo.active_game(PLAYER)
        game = guess.game_of(row.state)
        game.missed = ["Раз", "Два"]
        game.turns = [
            guess.Turn(names=["Три"], card="major_17", reading="Карты называют")
        ]
        await repo.save_game(row, guess.game_json(game), guess.ACTIVE)
    return await client.post("/api/guess/answer", headers=auth(), json={"verdict": "no"})


@pytest.mark.asyncio
async def test_a_lost_paid_party_gives_the_stars_back(make_client):
    client = await make_client(FakeGuessing())
    row = await paid_party(client, None)
    assert row.payment_id is not None  # партия помнит, чем за неё заплатили

    data = await (await lose_the_party(client)).json()

    assert data["game"]["status"] == guess.LOST
    assert data["game"]["refund"] == {"amount": 100, "unit": "⭐"}
    assert client.robot.refunds == [(PLAYER, "pay-1")]

    async with client.sessions() as session:
        # Возвращённое не считается выручкой
        assert await Repo(session).payments_summary() == {"count": 0, "totals": {}}


@pytest.mark.asyncio
async def test_a_won_party_keeps_the_stars(make_client):
    client = await make_client(FakeGuessing(lead_turn("Фредди Меркьюри")))
    await paid_party(client, None)

    data = await (
        await client.post("/api/guess/answer", headers=auth(), json={"verdict": "yes"})
    ).json()

    assert data["game"]["status"] == guess.WON
    assert "refund" not in data["game"]
    assert client.robot.refunds == []


@pytest.mark.asyncio
async def test_a_free_party_has_nothing_to_give_back(make_client):
    client = await make_client(FakeGuessing())
    await client.post("/api/guess", headers=auth())

    data = await (await lose_the_party(client)).json()

    assert data["game"]["status"] == guess.LOST
    assert "refund" not in data["game"]
    assert client.robot.refunds == []


@pytest.mark.asyncio
async def test_the_guarantee_runs_out_after_the_set_number(make_client):
    """«Не угадал» нажимает игрок: без предохранителя игра стала бы бесплатной."""
    client = await make_client(FakeGuessing(), GUESS_REFUNDS_MAX=0)
    await paid_party(client, None)

    data = await (await lose_the_party(client)).json()

    assert data["game"]["status"] == guess.LOST
    assert "refund" not in data["game"]
    assert client.robot.refunds == []


@pytest.mark.asyncio
async def test_a_refusal_from_telegram_does_not_break_the_ending(make_client):
    client = await make_client(FakeGuessing(), robot=FakeBot(broken=True))
    await paid_party(client, None)

    res = await lose_the_party(client)
    assert res.status == 200
    data = await res.json()
    assert data["game"]["status"] == guess.LOST
    assert "refund" not in data["game"]


@pytest.mark.asyncio
async def test_nothing_about_the_guarantee_leaks_before_the_end(make_client):
    """Узнать о возврате можно только проиграв: в этом вся затея."""
    client = await make_client(FakeGuessing())
    await paid_party(client, None)

    state = await (await client.get("/api/guess", headers=auth())).json()
    assert "refund" not in json.dumps(state, ensure_ascii=False)
    assert "верн" not in json.dumps(state, ensure_ascii=False).lower()


@pytest.mark.asyncio
async def test_the_third_loss_in_a_row_is_not_paid_back(make_client):
    """Два возврата на человека: дальше гарантия кончается, и молча."""
    client = await make_client(FakeGuessing())

    async with client.sessions() as session:
        repo = Repo(session)
        # Два возврата уже были
        for number in (1, 2):
            payment = await repo.add_payment(
                user_id=PLAYER, amount=100, currency="XTR",
                charge_id=f"old-{number}", kind="guess_game",
            )
            await repo.mark_refunded(payment)

    await paid_party(client, None)
    data = await (await lose_the_party(client)).json()

    assert data["game"]["status"] == guess.LOST
    assert "refund" not in data["game"]
    assert client.robot.refunds == []


@pytest.mark.asyncio
async def test_the_refund_is_still_there_when_the_app_is_reopened(make_client):
    """Закрыл приложение сразу после проигрыша — всё равно должен узнать."""
    client = await make_client(FakeGuessing())
    await paid_party(client, None)
    await lose_the_party(client)

    state = await (await client.get("/api/guess", headers=auth())).json()
    assert state["game"]["refund"] == {"amount": 100, "unit": "⭐"}


# --- партия, у которой оборвался ход ----------------------------------------


@pytest.mark.asyncio
async def test_a_party_whose_turn_never_came_can_be_resumed(make_client):
    """Модель была занята: оценка сохранилась, ход — нет. Это не тупик."""
    guessing = FakeGuessing(question_turn("Он жив?"))
    client = await make_client(guessing)
    await client.post("/api/guess", headers=auth())

    guessing.broken = True
    assert (
        await client.post("/api/guess/answer", headers=auth(), json={"verdict": "yes"})
    ).status == 503

    state = await (await client.get("/api/guess", headers=auth())).json()
    assert state["game"]["status"] == "active"
    assert state["game"]["turn"] is None  # ход не состоялся
    assert state["game"]["history"][0]["verdict"] == "yes"  # а оценка цела

    guessing.broken = False
    data = await (await client.post("/api/guess", headers=auth())).json()
    assert data["game"]["turn"]["question"] == "Вопрос 3?"
    assert data["game"]["asked"] == 2


@pytest.mark.asyncio
async def test_resuming_costs_nothing_and_starts_no_second_party(make_client):
    client = await make_client(FakeGuessing())
    await client.post("/api/guess", headers=auth())
    await client.post("/api/guess", headers=auth())

    async with client.sessions() as session:
        assert await Repo(session).count_games(PLAYER) == 1


@pytest.mark.asyncio
async def test_a_party_still_stuck_says_so_instead_of_pretending(make_client):
    guessing = FakeGuessing(question_turn())
    client = await make_client(guessing)
    await client.post("/api/guess", headers=auth())

    guessing.broken = True
    await client.post("/api/guess/answer", headers=auth(), json={"verdict": "yes"})

    res = await client.post("/api/guess", headers=auth())
    assert res.status == 503
    assert (await res.json())["error"] == "no_model"


# --- выключенная игра -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_switched_off_game_cannot_be_started_through_the_address(make_client):
    """Убрать с экрана — не то же самое, что закрыть: адрес знают и так."""
    client = await make_client(FakeGuessing(), GUESS_ENABLED=False)

    res = await client.post("/api/guess", headers=auth())
    assert res.status == 404
    assert (await res.json())["error"] == "not_found"

    async with client.sessions() as session:
        assert await Repo(session).count_games(PLAYER) == 0


@pytest.mark.asyncio
async def test_the_shop_front_says_whether_the_game_is_on(make_client):
    off = await make_client(FakeGuessing(), GUESS_ENABLED=False)
    assert (await (await off.get("/api/config")).json())["guess"] is False

    on = await make_client(FakeGuessing())
    assert (await (await on.get("/api/config")).json())["guess"] is True
