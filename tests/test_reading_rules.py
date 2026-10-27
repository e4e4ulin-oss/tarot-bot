"""Правила расклада, общие для чата и мини-приложения.

Это тот самый код, который скоро позовут из двух мест. Если правила разъедутся,
в приложении окажется больше бесплатных раскладов, чем в чате, и узнается это
по выручке, а не по тесту. Поэтому они проверяются отдельно от Telegram.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from bot.config import Settings
from bot.db import Repo
from bot.db.session import create_engine, create_session_factory, init_models
from bot.services import reading
from bot.tarot import get_spread

USER = 700
SPREAD = get_spread("ptf")


def make_settings(**extra) -> Settings:
    return Settings(BOT_TOKEN="123:abc", XAI_API_KEY="", **extra)


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'rules.db'}")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session)
        await repo.upsert_user(USER, None, "Клиент")
        yield repo
    await engine.dispose()


async def ask(repo: Repo, settings: Settings, *, number: int = 7, question: str | None = None):
    return await reading.start(repo, settings, USER, SPREAD, question, number=number)


@pytest.mark.asyncio
async def test_without_a_subscription_there_are_fewer_free_readings(repo):
    """Не запрет, а короткая полоса: два расклада вместо пяти."""
    settings = make_settings(FREE_AUTO_READINGS=5, FREE_WITHOUT_DAILY=2)

    decision = await ask(repo, settings)

    assert isinstance(decision, reading.Started)
    assert decision.access.free_left == 2


@pytest.mark.asyncio
async def test_a_subscriber_gets_the_whole_spread(repo):
    await repo.set_daily_subscription(USER, True)

    decision = await ask(repo, make_settings(FREE_AUTO_READINGS=5))

    assert isinstance(decision, reading.Started)
    assert len(decision.drawn) == SPREAD.size
    assert decision.access.reason == "free"
    assert decision.access.free_left == 5


@pytest.mark.asyncio
async def test_two_free_a_day_and_the_rest_tomorrow(repo):
    """Потолок считает только бесплатные и не отнимает их — переносит."""
    settings = make_settings(FREE_AUTO_READINGS=5, FREE_DAILY_LIMIT=2)
    await repo.set_daily_subscription(USER, True)

    for _ in range(2):
        assert isinstance(await ask(repo, settings), reading.Started)
        await repo.save_reading(
            user_id=USER, spread_key="ptf", question=None, cards=[],
            interpretation="", ai_used=False,
        )

    stopped = await ask(repo, settings)
    assert isinstance(stopped, reading.Refused)
    assert stopped.reason == "free_daily_limit"
    assert stopped.free_left == 3  # три ждут завтрашнего дня


@pytest.mark.asyncio
async def test_the_one_who_paid_hits_no_ceiling(repo):
    """Заплатил — раскладывает сколько нужно: потолок придуман для бесплатных."""
    settings = make_settings(FREE_AUTO_READINGS=0, FREE_DAILY_LIMIT=2)
    for _ in range(3):
        await repo.save_reading(
            user_id=USER, spread_key="ptf", question=None, cards=[],
            interpretation="", ai_used=False,
        )
    await repo.add_payment(USER, 369, "RUB", "charge-1")

    decision = await ask(repo, settings)

    assert isinstance(decision, reading.Started)
    assert decision.access.reason == "paid"


@pytest.mark.asyncio
async def test_when_the_free_ones_run_out_the_answer_is_pay(repo):
    """Звёздам не нужен ни провайдер, ни его одобрение: платить есть чем всегда."""
    decision = await ask(repo, make_settings(FREE_AUTO_READINGS=0))

    assert isinstance(decision, reading.Refused)
    assert decision.reason == "needs_payment"


@pytest.mark.asyncio
async def test_a_price_of_zero_is_a_dead_end_and_says_so(repo):
    """Незаполненная цена — не бесплатный товар: счёт на ноль звёзд не выставить."""
    settings = make_settings(FREE_AUTO_READINGS=0, AUTO_READING_STARS=0)
    assert (await ask(repo, settings)).reason == "payment_not_ready"


@pytest.mark.asyncio
async def test_the_daily_limit_stops_the_spread(repo):
    settings = make_settings(FREE_AUTO_READINGS=50, FREE_NEEDS_DAILY=False, DAILY_AUTO_LIMIT=2)

    for _ in range(2):
        assert isinstance(await ask(repo, settings), reading.Started)
        await repo.save_reading(
            user_id=USER, spread_key="ptf", question=None, cards=[],
            interpretation="", ai_used=False,
        )

    decision = await ask(repo, settings)
    assert isinstance(decision, reading.Refused)
    assert decision.reason == "daily_limit" and decision.limit == 2


@pytest.mark.asyncio
async def test_the_same_number_in_the_same_minute_gives_the_same_cards(repo):
    """Число человека решает расклад, а не случайность сервера.

    Это обещание, данное в чате, и в приложении оно обязано остаться тем же.
    """
    settings = make_settings(FREE_AUTO_READINGS=50, FREE_NEEDS_DAILY=False)

    first = await ask(repo, settings, number=17, question="про работу")
    again = await ask(repo, settings, number=17, question="про работу")
    other = await ask(repo, settings, number=18, question="про работу")

    assert [card.card.id for card in first.drawn] == [card.card.id for card in again.drawn]
    assert [card.card.id for card in first.drawn] != [card.card.id for card in other.drawn]


@pytest.mark.asyncio
async def test_the_owner_is_never_refused(repo):
    settings = make_settings(FREE_AUTO_READINGS=0, ADMIN_IDS=str(USER))

    assert isinstance(await ask(repo, settings), reading.Started)
