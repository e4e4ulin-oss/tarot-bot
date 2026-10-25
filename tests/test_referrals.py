"""Приглашения: кто кого привёл и когда за это платят.

Награда — бесплатные расклады, то есть деньги в конечном счёте. Поэтому здесь
проверяется не только «начислилось», но и все способы начислить себе лишнего:
своя же ссылка, повторный вход, чужой бот, ссылка без расклада.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from bot import referrals
from bot.access import check
from bot.config import Settings
from bot.db import Repo
from bot.db.session import create_engine, create_session_factory, init_models

HOST = 100  # тот, кто приглашает
GUEST = 200  # тот, кто пришёл по ссылке


def make_settings(**extra) -> Settings:
    return Settings(BOT_TOKEN="123:abc", XAI_API_KEY="", **extra)


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'ref.db'}")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session)
        await repo.upsert_user(HOST, None, "Хозяин ссылки")
        await repo.upsert_user(GUEST, None, "Гость")
        yield repo
    await engine.dispose()


async def make_reading(repo: Repo, user_id: int) -> None:
    await repo.save_reading(
        user_id=user_id, spread_key="day", question=None, cards=[],
        interpretation="", ai_used=False,
    )


def test_the_link_carries_the_number_and_reads_back():
    link = referrals.link_for("Taroautorasklad_bot", 42)

    assert link.endswith("?start=ref42")
    assert referrals.inviter_from("ref42") == 42
    assert referrals.inviter_from("ref") is None
    assert referrals.inviter_from("deck") is None
    assert referrals.inviter_from(None) is None


@pytest.mark.asyncio
async def test_the_guest_is_greeted_and_the_inviter_remembered(repo):
    settings = make_settings(REFERRAL_WELCOME=1)

    joined = await referrals.join(repo, settings, GUEST, referrals.payload_for(HOST))

    assert joined is not None and joined.inviter_id == HOST and joined.welcome == 1
    assert (await repo.get_user(GUEST)).bonus_readings == 1
    assert await repo.invited_count(HOST) == 1


@pytest.mark.asyncio
async def test_your_own_link_brings_nothing(repo):
    assert await referrals.join(repo, make_settings(), HOST, referrals.payload_for(HOST)) is None
    assert (await repo.get_user(HOST)).bonus_readings == 0


@pytest.mark.asyncio
async def test_a_second_visit_does_not_pay_again(repo):
    settings = make_settings(REFERRAL_WELCOME=1)
    await referrals.join(repo, settings, GUEST, referrals.payload_for(HOST))

    assert await referrals.join(repo, settings, GUEST, referrals.payload_for(HOST)) is None
    assert (await repo.get_user(GUEST)).bonus_readings == 1


@pytest.mark.asyncio
async def test_a_stranger_from_another_bot_is_not_an_inviter(repo):
    """Номера клиентов у каждого бренда свои: чужой здесь ничего не значит."""
    assert await referrals.join(repo, make_settings(), GUEST, "ref999999") is None
    assert (await repo.get_user(GUEST)).invited_by is None


@pytest.mark.asyncio
async def test_the_reward_waits_for_the_first_reading(repo):
    settings = make_settings(REFERRAL_BONUS=2, REFERRAL_WELCOME=1)
    await referrals.join(repo, settings, GUEST, referrals.payload_for(HOST))

    # Пришёл, но не разложил — пригласившему пока ничего
    assert await referrals.reward(repo, settings, GUEST) is None
    assert (await repo.get_user(HOST)).bonus_readings == 0

    await make_reading(repo, GUEST)
    assert await referrals.reward(repo, settings, GUEST) == (HOST, 2)
    assert (await repo.get_user(HOST)).bonus_readings == 2


@pytest.mark.asyncio
async def test_the_second_reading_pays_nothing_more(repo):
    settings = make_settings(REFERRAL_BONUS=2)
    await referrals.join(repo, settings, GUEST, referrals.payload_for(HOST))
    await make_reading(repo, GUEST)
    await referrals.reward(repo, settings, GUEST)

    await make_reading(repo, GUEST)
    assert await referrals.reward(repo, settings, GUEST) is None
    assert (await repo.get_user(HOST)).bonus_readings == 2


@pytest.mark.asyncio
async def test_the_bonus_lengthens_the_free_stretch(repo):
    """Бонус — не вторая валюта: бесплатная полоса просто длиннее."""
    settings = make_settings(FREE_AUTO_READINGS=1, FREE_NEEDS_DAILY=False)
    await make_reading(repo, HOST)

    spent = await check(repo, settings, HOST)
    assert not spent.allowed and spent.reason == "needs_payment"

    await repo.add_bonus(HOST, 2)
    access = await check(repo, settings, HOST)
    assert access.allowed and access.free_left == 2
