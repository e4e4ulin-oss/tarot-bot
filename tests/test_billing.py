"""Чем платят и сколько: выбор способа, арифметика счёта, сложение сумм.

Главная ловушка здесь — копейки. Рубли Telegram считает сотыми долями, а
звёзды штуками, и одно умножение не на то превратило бы 250 звёзд в 25 000.
Поэтому арифметика проверяется отдельно от всего остального.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from bot import billing
from bot.config import Settings
from bot.db import Repo, create_engine, create_session_factory, init_models
from bot.handlers.payments import on_paid


def make_settings(**extra) -> Settings:
    return Settings(BOT_TOKEN="123:abc", XAI_API_KEY="", **extra)


def test_stars_need_nobody_s_permission():
    """По умолчанию платят звёздами: им не нужен ни провайдер, ни токен."""
    assert billing.method(make_settings()) == billing.STARS


def test_the_provider_without_a_token_is_not_a_way_to_pay():
    chosen = make_settings(PAYMENT_METHOD="provider")
    assert billing.method(chosen) is None
    assert billing.terms(chosen) is None

    ready = make_settings(PAYMENT_METHOD="provider", PAYMENT_PROVIDER_TOKEN="1:LIVE:x")
    assert billing.method(ready) == billing.PROVIDER


def test_an_unknown_method_falls_back_to_stars():
    """Опечатка в настройке не должна оставлять бота без приёма денег."""
    assert billing.method(make_settings(PAYMENT_METHOD="звёзды")) == billing.STARS


def test_the_price_comes_in_the_currency_that_is_charged():
    stars = billing.terms(make_settings(AUTO_READING_STARS=250), billing.PAYLOAD_AUTO)
    assert (stars.price, stars.unit, stars.currency) == (250, "⭐", "XTR")

    rubles = billing.terms(
        make_settings(
            PAYMENT_METHOD="provider",
            PAYMENT_PROVIDER_TOKEN="1:LIVE:x",
            AUTO_READING_PRICE=369,
        ),
        billing.PAYLOAD_AUTO,
    )
    assert (rubles.price, rubles.unit, rubles.currency) == (369, "₽", "RUB")


def test_the_deep_reading_is_priced_apart_from_the_spread():
    settings = make_settings(AUTO_READING_STARS=250, NATAL_READING_STARS=650)
    assert billing.terms(settings, billing.PAYLOAD_AUTO).price == 250
    assert billing.terms(settings, billing.PAYLOAD_NATAL).price == 650


def test_a_price_of_zero_is_an_empty_setting_not_a_gift():
    assert billing.terms(make_settings(AUTO_READING_STARS=0)) is None


def test_stars_are_counted_in_whole_ones_and_roubles_in_kopecks():
    stars = billing.terms(make_settings(AUTO_READING_STARS=250))
    kwargs = billing.invoice_kwargs(make_settings(), stars)
    assert kwargs["currency"] == "XTR"
    assert [price.amount for price in kwargs["prices"]] == [250]
    # Для звёзд токена нет и быть не должно
    assert kwargs["provider_token"] == ""

    paying = make_settings(
        PAYMENT_METHOD="provider", PAYMENT_PROVIDER_TOKEN="1:LIVE:x", AUTO_READING_PRICE=369
    )
    kwargs = billing.invoice_kwargs(paying, billing.terms(paying))
    assert kwargs["currency"] == "RUB"
    assert [price.amount for price in kwargs["prices"]] == [36900]
    assert kwargs["provider_token"] == "1:LIVE:x"


def test_the_invoice_names_the_goods():
    """Счёт за расклад и счёт за разбор карты — разные товары, и это видно."""
    settings = make_settings()
    spread = billing.invoice_kwargs(settings, billing.terms(settings, billing.PAYLOAD_AUTO))
    deep = billing.invoice_kwargs(settings, billing.terms(settings, billing.PAYLOAD_NATAL))

    assert spread["payload"] == "auto_reading"
    assert deep["payload"] == "natal_reading"
    assert spread["title"] != deep["title"]


def test_what_arrived_is_read_in_the_same_units():
    assert billing.received("XTR", 250) == 250
    assert billing.received("RUB", 36900) == 369


def test_roubles_and_stars_stand_side_by_side_and_do_not_add_up():
    settings = make_settings()
    assert billing.money({"RUB": 3000, "XTR": 1250}, settings) == "3000 ₽ и 1250 ⭐"
    assert billing.money({"XTR": 1250}, settings) == "1250 ⭐"
    # Пустая касса — всё равно сумма, а не пустое место
    assert billing.money({}, settings) == "0 ₽"
    assert billing.money({"RUB": 0}, settings) == "0 ₽"


# --- что записывается после оплаты -----------------------------------------
# Деление на сто живёт именно здесь, и до сих пор этот путь не был покрыт
# ничем: звезда, записанная как копейка, превратила бы 250 ⭐ в 2.


class FakeUser:
    id = 77


class FakePayment:
    def __init__(self, currency: str, total_amount: int, payload: str) -> None:
        self.currency = currency
        self.total_amount = total_amount
        self.invoice_payload = payload
        self.telegram_payment_charge_id = f"charge-{currency}-{total_amount}"


class FakeMessage:
    def __init__(self, payment: FakePayment) -> None:
        self.from_user = FakeUser()
        self.successful_payment = payment
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append(text)


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'billing.db'}")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        yield Repo(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_star_payment_is_recorded_in_stars(repo):
    message = FakeMessage(FakePayment("XTR", 250, "auto_reading"))
    await on_paid(message, repo, make_settings())

    summary = await repo.payments_summary()
    assert summary == {"count": 1, "totals": {"XTR": 250}}


@pytest.mark.asyncio
async def test_a_rouble_payment_is_recorded_without_kopecks(repo):
    message = FakeMessage(FakePayment("RUB", 36900, "auto_reading"))
    await on_paid(message, repo, make_settings())

    summary = await repo.payments_summary()
    assert summary == {"count": 1, "totals": {"RUB": 369}}


@pytest.mark.asyncio
async def test_paying_for_the_deep_reading_does_not_open_a_spread(repo):
    """Товары разные: оплата разбора не должна считаться оплатой расклада."""
    message = FakeMessage(FakePayment("XTR", 650, "natal_reading"))
    await on_paid(message, repo, make_settings())

    assert await repo.unspent_payment(77, "natal_reading") is not None
    assert await repo.unspent_payment(77, "auto_reading") is None


@pytest.mark.asyncio
async def test_a_refunded_payment_opens_nothing_and_counts_for_nothing(repo):
    """Возврат — не подарок: деньги ушли обратно, товар закрывается тоже."""
    message = FakeMessage(FakePayment("XTR", 250, "auto_reading"))
    await on_paid(message, repo, make_settings())

    payment = await repo.payment_by_charge("charge-XTR-250")
    assert payment is not None
    await repo.mark_refunded(payment)

    assert await repo.unspent_payment(77, "auto_reading") is None
    assert await repo.payments_summary() == {"count": 0, "totals": {}}


@pytest.mark.asyncio
async def test_the_last_payments_come_newest_first(repo):
    for amount, kind in ((250, "auto_reading"), (650, "natal_reading")):
        await on_paid(FakeMessage(FakePayment("XTR", amount, kind)), repo, make_settings())
    rows = await repo.last_payments(10)
    assert [row.amount for row in rows] == [650, 250]
