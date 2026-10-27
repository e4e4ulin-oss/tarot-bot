"""Сколько стоит товар и как из этого собирается счёт.

Платят звёздами Telegram, и только ими. Так велят правила Telegram для
цифровых услуг: валюта XTR, ровно одна строка в счёте, токен провайдера не
нужен вовсе. Рублёвый провайдер здесь был и убран: он этим правилам
противоречит, а эквайеры к тому же нервно относятся к эзотерике.

Деньги приходят на звёздный баланс владельца бота — в франшизной схеме это
баланс франчайзи, и ничего настраивать для этого не требуется.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aiogram.types import LabeledPrice

from . import texts
from .config import Settings

# Валюта звёзд по Bot API и знак, которым их показывают человеку
STARS_CURRENCY = "XTR"
STARS_SIGN = "⭐"

# Что именно оплачено. Расклад и разбор карты — разные товары, и оплата
# одного не должна открывать другой.
PAYLOAD_AUTO = "auto_reading"
PAYLOAD_NATAL = "natal_reading"


@dataclass(frozen=True)
class Terms:
    """Условия одной покупки: сколько и за что."""

    payload: str
    price: int
    unit: str
    currency: str
    title: str
    description: str


def terms(settings: Settings, payload: str = PAYLOAD_AUTO) -> Terms | None:
    """Во что обойдётся товар. None — платить нечем."""
    natal = payload == PAYLOAD_NATAL
    price = settings.natal_reading_stars if natal else settings.auto_reading_stars
    if price <= 0:
        # Нулевая цена — не бесплатный товар, а незаполненная настройка:
        # Telegram такой счёт всё равно не примет
        return None

    return Terms(
        payload=payload,
        price=price,
        unit=STARS_SIGN,
        currency=STARS_CURRENCY,
        title=texts.INVOICE_NATAL_TITLE if natal else texts.INVOICE_TITLE,
        description=texts.INVOICE_NATAL_DESCRIPTION if natal else texts.INVOICE_DESCRIPTION,
    )


def invoice_kwargs(deal: Terms) -> dict[str, object]:
    """Аргументы счёта — одинаковые для answer_invoice и create_invoice_link.

    Сумма в звёздах считается штуками. Привычное для денег умножение на сто
    превратило бы 50 звёзд в пять тысяч, поэтому его здесь нет и не было бы
    зачем: копейки у звезды отсутствуют.
    """
    return {
        "title": deal.title,
        "description": deal.description,
        "payload": deal.payload,
        "currency": deal.currency,
        "prices": [LabeledPrice(label=deal.title, amount=deal.price)],
        # Для звёзд токена нет и быть не должно — Bot API ждёт здесь пустоту
        "provider_token": "",
    }


def sign(currency: str, settings: Settings) -> str:
    """Чем подписывают сумму в этой валюте.

    Рубли здесь остались не зря: ими оплачивали расклады до перехода на
    звёзды, и в старой статистике эти платежи никуда не делись.
    """
    return STARS_SIGN if currency == STARS_CURRENCY else settings.author_currency


def money(totals: Mapping[str, int], settings: Settings) -> str:
    """Сумма вида «3000 ₽ и 1250 ⭐».

    Складывать рубли со звёздами нельзя, а показать одно число хочется:
    поэтому валюты перечисляются рядом, а не суммируются.
    """
    parts = [
        f"{amount} {sign(code, settings)}"
        for code, amount in sorted(totals.items())
        if amount
    ]
    return " и ".join(parts) or f"0 {settings.author_currency}"


def received(currency: str, total_amount: int) -> int:
    """Сколько пришло, в единицах той же валюты, что и у счёта."""
    if currency == STARS_CURRENCY:
        return total_amount
    # Старые рублёвые платежи Telegram считал копейками
    return total_amount // 100
