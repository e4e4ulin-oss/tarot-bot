"""Доступ к авто-раскладу: бесплатные, подписка, оплата."""

import pytest
import pytest_asyncio

from bot.access import check, spend
from bot.config import Settings
from bot.db import Repo
from bot.db.session import create_engine, create_session_factory, init_models

USER = 500


def make_settings(**extra) -> Settings:
    return Settings(BOT_TOKEN="123:abc", XAI_API_KEY="", **extra)


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session)
        await repo.upsert_user(USER, None, "Клиент")
        yield repo
    await engine.dispose()


async def make_reading(repo: Repo, user_id: int = USER) -> None:
    await repo.save_reading(
        user_id=user_id, spread_key="day", question=None, cards=[],
        interpretation="", ai_used=False,
    )


@pytest.mark.asyncio
async def test_the_subscription_decides_how_many_are_free(repo):
    """Подписан — пять, без подписки — два. Это не запрет, а длина полосы."""
    settings = make_settings(FREE_AUTO_READINGS=5, FREE_WITHOUT_DAILY=2)

    without = await check(repo, settings, USER)
    assert without.allowed and without.reason == "free" and without.free_left == 2

    await repo.set_daily_subscription(USER, True)
    access = await check(repo, settings, USER)
    assert access.allowed and access.free_left == 5


@pytest.mark.asyncio
async def test_what_is_spent_does_not_come_back_with_the_subscription(repo):
    """Отписался на третьем раскладе — остался ни с чем; вернулся — остаток цел.

    Считается от текущей подписки, а потраченное вычитается: никакого
    отдельного кошелька, который можно было бы обнулить или восстановить.
    """
    settings = make_settings(FREE_AUTO_READINGS=5, FREE_WITHOUT_DAILY=2)
    await repo.set_daily_subscription(USER, True)
    for _ in range(3):
        await make_reading(repo)

    assert (await check(repo, settings, USER)).free_left == 2

    await repo.set_daily_subscription(USER, False)
    gone = await check(repo, settings, USER)
    assert not gone.allowed and gone.reason == "needs_payment" and gone.free_left == 0

    await repo.set_daily_subscription(USER, True)
    assert (await check(repo, settings, USER)).free_left == 2


@pytest.mark.asyncio
async def test_the_warning_knows_what_is_at_stake(repo):
    """Перед отключением человеку говорят, сколько он потеряет."""
    settings = make_settings(FREE_AUTO_READINGS=5, FREE_WITHOUT_DAILY=2)
    await repo.set_daily_subscription(USER, True)
    await make_reading(repo)

    access = await check(repo, settings, USER)
    assert access.free_left == 4  # сейчас
    assert access.without_daily == 1  # останется, если отключить

    await make_reading(repo)
    await make_reading(repo)
    assert (await check(repo, settings, USER)).without_daily == 0


@pytest.mark.asyncio
async def test_free_readings_run_out(repo):
    settings = make_settings(FREE_AUTO_READINGS=3)
    await repo.set_daily_subscription(USER, True)

    for expected_left in (3, 2, 1):
        access = await check(repo, settings, USER)
        assert access.allowed and access.free_left == expected_left
        await make_reading(repo)

    access = await check(repo, settings, USER)
    assert not access.allowed and access.reason == "needs_payment"


@pytest.mark.asyncio
async def test_payment_opens_exactly_one_reading(repo):
    settings = make_settings(FREE_AUTO_READINGS=0)
    await repo.add_payment(USER, 369, "RUB", "charge-1")

    access = await check(repo, settings, USER)
    assert access.allowed and access.reason == "paid"

    # Пока расклад не состоялся, оплата не списана
    assert (await check(repo, settings, USER)).allowed

    await spend(repo, access)
    assert (await check(repo, settings, USER)).reason == "needs_payment"


@pytest.mark.asyncio
async def test_payments_are_counted_for_the_owner(repo):
    await repo.add_payment(USER, 369, "RUB", "charge-2")
    await repo.add_payment(USER, 369, "RUB", "charge-3")
    assert await repo.payments_summary() == {"count": 2, "totals": {"RUB": 738}}


@pytest.mark.asyncio
async def test_the_owner_is_not_counted(repo):
    """Лимит придуман для клиентов: владельцу он мешает смотреть свой же бот."""
    settings = make_settings(FREE_AUTO_READINGS=1, ADMIN_IDS=str(USER))

    for _ in range(5):
        access = await check(repo, settings, USER)
        assert access.allowed and access.reason == "owner"
        await make_reading(repo)

    # Обычному человеку те же пять раскладов уже перекрыли бы доступ
    other = 501
    await repo.upsert_user(other, None, "Клиент")
    await repo.set_daily_subscription(other, True)
    assert await repo.count_auto_readings(other) == 0
    assert (await check(repo, settings, other)).reason == "free"


@pytest.mark.asyncio
async def test_admin_chat_owner_is_free_too(repo):
    """Владелец мог не вписать себя в ADMIN_IDS — но заявки идут ему в личку."""
    settings = make_settings(FREE_AUTO_READINGS=0, ADMIN_CHAT_ID=str(USER))
    assert (await check(repo, settings, USER)).reason == "owner"
