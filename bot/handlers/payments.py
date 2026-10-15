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
from ..keyboards import after_reading_menu

logger = logging.getLogger(__name__)

router = Router(name="payments")

# Что именно оплачено — пригодится, когда товаров станет больше одного
PAYLOAD_AUTO = "auto_reading"


@router.callback_query(F.data == "pay:auto")
async def cb_pay(call: CallbackQuery, settings: Settings) -> None:
    if not isinstance(call.message, Message):
        await call.answer()
        return

    if not settings.payment_provider_token:
        await call.message.answer(texts.PAYMENT_NOT_READY.format(author=settings.author_name))
        await call.answer()
        return

    await call.message.answer_invoice(
        title=texts.INVOICE_TITLE,
        description=texts.INVOICE_DESCRIPTION,
        payload=PAYLOAD_AUTO,
        provider_token=settings.payment_provider_token,
        currency=settings.payment_currency,
        # Telegram считает сумму в копейках
        prices=[
            LabeledPrice(label=texts.INVOICE_TITLE, amount=settings.auto_reading_price * 100)
        ],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    # Отказывать здесь нечему: товар цифровой и всегда доступен
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message, repo: Repo, settings: Settings) -> None:
    payment = message.successful_payment
    await repo.add_payment(
        user_id=message.from_user.id,
        amount=payment.total_amount // 100,
        currency=payment.currency,
        charge_id=payment.telegram_payment_charge_id,
    )
    await repo.log_event(message.from_user.id, analytics.PAID)
    logger.info("Оплата: %s %s от %s", payment.total_amount // 100, payment.currency,
                message.from_user.id)
    await message.answer(texts.PAID_THANKS, reply_markup=after_reading_menu(settings.author_name))
