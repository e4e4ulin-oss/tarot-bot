"""Промокоды: каждый отказ называется своим словом.

«Код не подошёл» — худший возможный ответ: человек не понимает, опечатался он,
опоздал или уже вводил. Поэтому причины проверяются по отдельности.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from bot import promo
from bot.db import Repo
from bot.db.session import create_engine, create_session_factory, init_models

USER = 300
OTHER = 301


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'promo.db'}")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = Repo(session)
        await repo.upsert_user(USER, None, "Клиент")
        await repo.upsert_user(OTHER, None, "Другой")
        yield repo
    await engine.dispose()


def today() -> object:
    return datetime.now(UTC).date()


def test_a_code_is_read_however_it_was_typed():
    assert promo.normalize(" осень ") == "ОСЕНЬ"
    assert promo.normalize("tarot 5") == "TAROT5"
    assert promo.normalize("—ОСЕНЬ—") == "ОСЕНЬ"


@pytest.mark.asyncio
async def test_a_good_code_adds_readings(repo):
    await repo.add_promo("ОСЕНЬ", readings=3)

    result = await promo.redeem(repo, USER, "осень")

    assert result.ok and result.readings == 3 and result.total == 3
    assert (await repo.get_user(USER)).bonus_readings == 3


@pytest.mark.asyncio
async def test_the_same_person_cannot_use_it_twice(repo):
    await repo.add_promo("ОСЕНЬ", readings=3)
    await promo.redeem(repo, USER, "ОСЕНЬ")

    again = await promo.redeem(repo, USER, "ОСЕНЬ")

    assert not again.ok and again.reason == "already"
    assert (await repo.get_user(USER)).bonus_readings == 3


@pytest.mark.asyncio
async def test_an_unknown_code_says_so(repo):
    result = await promo.redeem(repo, USER, "НЕБЫЛО")
    assert not result.ok and result.reason == "unknown"

    assert (await promo.redeem(repo, USER, "   ")).reason == "empty"


@pytest.mark.asyncio
async def test_an_expired_code_is_not_a_typo(repo):
    await repo.add_promo("ВЧЕРА", readings=1, expires_at=today() - timedelta(days=1))

    result = await promo.redeem(repo, USER, "ВЧЕРА")

    assert not result.ok and result.reason == "expired"


@pytest.mark.asyncio
async def test_the_last_use_closes_the_code(repo):
    await repo.add_promo("ПЕРВЫМ", readings=1, uses_limit=1)

    assert (await promo.redeem(repo, USER, "ПЕРВЫМ")).ok
    late = await promo.redeem(repo, OTHER, "ПЕРВЫМ")

    assert not late.ok and late.reason == "spent"
    assert (await repo.get_user(OTHER)).bonus_readings == 0


@pytest.mark.asyncio
async def test_a_switched_off_code_stops_working(repo):
    code = await repo.add_promo("ПАУЗА", readings=1)
    await repo.set_promo_active(code, False)

    assert (await promo.redeem(repo, USER, "ПАУЗА")).reason == "disabled"


@pytest.mark.asyncio
async def test_a_code_belongs_to_its_brand(repo):
    """У франчайзи может быть то же слово с другой щедростью."""
    await repo.add_promo("ОСЕНЬ", readings=3)
    stranger = Repo(repo.session, tenant_id=2)

    assert (await promo.redeem(stranger, USER, "ОСЕНЬ")).reason == "unknown"
