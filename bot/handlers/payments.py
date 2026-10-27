"""Оплата авто-расклада и разбора карты: звёзды Telegram или рубли.

Деньги идут туда, чей бот выставил счёт: в рублях — на счёт провайдера, в
звёздах — на звёздный баланс владельца бота. В франшизной схеме и то и другое
принадлежит франчайзи. В базе остаётся только след платежа: по нему видно,
сколько и когда занесли, и по нему же открывается один расклад.

Чем именно платят, решает bot.billing. Если платить нечем — выбран провайдер,
а токена нет, — бот честно говорит, что оплата пока не подключена, вместо
кнопки, которая никуда не ведёт.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from .. import analytics, billing, texts
from ..billing import PAYLOAD_AUTO, PAYLOAD_NATAL
from ..config import Settings
from ..db import Repo
from ..keyboards import after_reading_menu, natal_ready_menu

logger = logging.getLogger(__name__)

router = Router(name="payments")

__all__ = ["router", "send_invoice", "PAYLOAD_AUTO", "PAYLOAD_NATAL"]


async def send_invoice(message: Message, settings: Settings, payload: str) -> bool:
    """Счёт на один товар. Возвращает, удалось ли его выставить."""
    deal = billing.terms(settings, payload)
    if deal is None:
        await message.answer(texts.PAYMENT_NOT_READY.format(author=settings.author_name))
        return False

    await message.answer_invoice(**billing.invoice_kwargs(settings, deal))
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
    amount = billing.received(payment.currency, payment.total_amount)
    await repo.add_payment(
        user_id=message.from_user.id,
        amount=amount,
        currency=payment.currency,
        charge_id=payment.telegram_payment_charge_id,
        kind=kind,
    )
    await repo.log_event(message.from_user.id, analytics.PAID)
    logger.info("Оплата: %s %s от %s", amount, payment.currency, message.from_user.id)
    if kind == PAYLOAD_NATAL:
        # Разбор начинается сразу: человек заплатил не за кнопку, а за текст
        await message.answer(texts.PAID_NATAL, reply_markup=natal_ready_menu())
        return

    await message.answer(texts.PAID_THANKS, reply_markup=after_reading_menu(settings.author_name))
