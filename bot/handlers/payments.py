"""Оплата авто-расклада через Telegram Payments.

Деньги идут на счёт того, чей провайдерский токен настроен, — в франшизной
схеме это счёт франчайзи. В базе остаётся только след платежа: по нему видно,
сколько и когда занесли, и по нему же открывается один расклад.

Без токена провайдера оплата не предлагается: бот честно говорит, что платные
расклады пока не подключены, вместо кнопки, которая никуда не ведёт.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from .. import analytics, texts
from ..config import Settings
from ..db import Repo
from ..keyboards import after_reading_menu, natal_ready_menu

logger = logging.getLogger(__name__)

router = Router(name="payments")

# Что именно оплачено — пригодится, когда товаров станет больше одного
PAYLOAD_AUTO = "auto_reading"
PAYLOAD_NATAL = "natal_reading"


async def send_invoice(message: Message, settings: Settings, payload: str) -> bool:
    """Счёт на один товар. Возвращает, удалось ли его выставить."""
    if not settings.payment_provider_token:
        await message.answer(texts.PAYMENT_NOT_READY.format(author=settings.author_name))
        return False

    natal = payload == PAYLOAD_NATAL
    title = texts.INVOICE_NATAL_TITLE if natal else texts.INVOICE_TITLE
    price = settings.natal_reading_price if natal else settings.auto_reading_price

    await message.answer_invoice(
        title=title,
        description=texts.INVOICE_NATAL_DESCRIPTION if natal else texts.INVOICE_DESCRIPTION,
        payload=payload,
        provider_token=settings.payment_provider_token,
        currency=settings.payment_currency,
        # Telegram считает сумму в копейках
        prices=[LabeledPrice(label=title, amount=price * 100)],
    )
    return True


@router.callback_query(F.data == "pay:auto")
async def cb_pay(call: CallbackQuery, settings: Settings) -> None:
    if isinstance(call.message, Message):
        await send_invoice(call.message, settings, PAYLOAD_AUTO)
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    # Отказывать здесь нечему: товар цифровой и всегда доступен
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message, repo: Repo, settings: Settings) -> None:
    payment = message.successful_payment
    kind = payment.invoice_payload or PAYLOAD_AUTO
    await repo.add_payment(
        user_id=message.from_user.id,
        amount=payment.total_amount // 100,
        currency=payment.currency,
        charge_id=payment.telegram_payment_charge_id,
        kind=kind,
    )
    await repo.log_event(message.from_user.id, analytics.PAID)
    logger.info("Оплата: %s %s от %s", payment.total_amount // 100, payment.currency,
                message.from_user.id)
    if kind == PAYLOAD_NATAL:
        # Разбор начинается сразу: человек заплатил не за кнопку, а за текст
        await message.answer(texts.PAID_NATAL, reply_markup=natal_ready_menu())
        return

    await message.answer(texts.PAID_THANKS, reply_markup=after_reading_menu(settings.author_name))
