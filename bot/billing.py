"""Чем и за сколько человек платит: звёздами Telegram или рублями.

Правила Telegram таковы, что цифровые услуги продаются за звёзды: валюта XTR,
ровно одна строка в счёте, токен провайдера не нужен вовсе. Но покупателю из
России звёзды ещё надо где-то купить, а рублёвый счёт он оплачивает картой или
по СБП сразу — поэтому способ оплаты здесь не выбран навсегда. Он лежит в
настройках арендатора и переключается без правки кода.

У каждого товара две цены: в рублях и в звёздах. Наружу отдаётся та, которой
платят сейчас, — чтобы в приложении и в чате не оказалось числа от одной
валюты со знаком другой.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aiogram.types import LabeledPrice

from . import texts
from .config import Settings

# Способы оплаты. Провайдер без токена — это «оплата не подключена», а не
# ошибка: так бот и ведёт себя сейчас, пока токена нет.
PROVIDER = "provider"
STARS = "stars"
METHODS = (PROVIDER, STARS)

# Валюта звёзд по Bot API и знак, которым их показывают человеку
STARS_CURRENCY = "XTR"
STARS_SIGN = "⭐"

# Что именно оплачено. Расклад и разбор карты — разные товары, и оплата
# одного не должна открывать другой.
PAYLOAD_AUTO = "auto_reading"
PAYLOAD_NATAL = "natal_reading"


@dataclass(frozen=True)
class Terms:
    """Условия одной покупки: чем платят, сколько и за что."""

    method: str
    payload: str
    price: int
    unit: str
    currency: str
    title: str
    description: str

    @property
    def stars(self) -> bool:
        return self.method == STARS


def method(settings: Settings) -> str | None:
    """Чем принимаем оплату. None — не принимаем совсем.

    Звёздам ничего не требуется, поэтому они доступны всегда, когда выбраны.
    Провайдеру нужен токен, и без него оплаты нет.
    """
    chosen = (settings.payment_method or STARS).strip().lower()
    if chosen == PROVIDER:
        return PROVIDER if settings.payment_provider_token else None
    return STARS


def terms(settings: Settings, payload: str = PAYLOAD_AUTO) -> Terms | None:
    """Во что обойдётся товар. None — платить нечем."""
    way = method(settings)
    if way is None:
        return None

    natal = payload == PAYLOAD_NATAL
    if way == STARS:
        price = settings.natal_reading_stars if natal else settings.auto_reading_stars
        unit, currency = STARS_SIGN, STARS_CURRENCY
    else:
        price = settings.natal_reading_price if natal else settings.auto_reading_price
        unit, currency = settings.author_currency, settings.payment_currency

    if price <= 0:
        # Нулевая цена — не бесплатный товар, а незаполненная настройка:
        # Telegram такой счёт всё равно не примет
        return None

    return Terms(
        method=way,
        payload=payload,
        price=price,
        unit=unit,
        currency=currency,
        title=texts.INVOICE_NATAL_TITLE if natal else texts.INVOICE_TITLE,
        description=texts.INVOICE_NATAL_DESCRIPTION if natal else texts.INVOICE_DESCRIPTION,
    )


def invoice_kwargs(settings: Settings, deal: Terms) -> dict[str, object]:
    """Аргументы счёта — одинаковые для answer_invoice и create_invoice_link.

    Разница между валютами здесь всего одна, но важная: рубли Telegram считает
    в копейках, а звёзды — штуками, и умножение на сто превратило бы 250 звёзд
    в двадцать пять тысяч.
    """
    amount = deal.price if deal.stars else deal.price * 100
    return {
        "title": deal.title,
        "description": deal.description,
        "payload": deal.payload,
        "currency": deal.currency,
        "prices": [LabeledPrice(label=deal.title, amount=amount)],
        # Для звёзд токена нет и быть не должно — Bot API ждёт здесь пустоту
        "provider_token": "" if deal.stars else settings.payment_provider_token,
    }


def sign(currency: str, settings: Settings) -> str:
    """Чем подписывают сумму в этой валюте."""
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
    return total_amount // 100
