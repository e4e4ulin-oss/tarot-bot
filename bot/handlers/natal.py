"""Подробный разбор натальной карты — платный.

Расчёт карты бесплатен и доступен всем: он ничего не стоит. Платят за текст
на несколько тысяч знаков, который пишется по этому расчёту.

Разбор пишется один раз. Карта рождения не меняется, значит и разбор
прежний: повторно платить незачем, старый отдаётся по первому слову.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from .. import texts
from ..config import Settings
from ..db import Repo
from ..db.models import ReadingMode
from ..keyboards import back_to_menu, natal_menu
from ..profile import describe
from ..services import GrokError, Interpreter
from ..utils import split_text
from .chart import card_for
from .payments import PAYLOAD_NATAL, send_invoice

logger = logging.getLogger(__name__)

router = Router(name="natal")


def free_for(settings: Settings, user_id: int) -> bool:
    return settings.is_admin(user_id) or user_id == settings.admin_chat_id


@router.message(Command("natal"))
async def cmd_natal(message: Message, repo: Repo, settings: Settings) -> None:
    await offer(message, repo, settings, message.chat.id)


@router.callback_query(F.data == "natal:offer")
async def cb_offer(call: CallbackQuery, repo: Repo, settings: Settings) -> None:
    if isinstance(call.message, Message):
        await offer(call.message, repo, settings, call.from_user.id)
    await call.answer()


async def offer(message: Message, repo: Repo, settings: Settings, user_id: int) -> None:
    card = await card_for(repo, user_id)
    if card is None or not card.has_houses:
        await message.answer(texts.NATAL_NEEDS_TIME, reply_markup=back_to_menu())
        return

    written = await repo.last_natal(user_id)
    if written is not None:
        await message.answer(texts.NATAL_AGAIN)
        for chunk in split_text(written.interpretation or ""):
            await message.answer(chunk)
        return

    await message.answer(
        texts.NATAL_OFFER.format(
            price=settings.natal_reading_price, currency=settings.author_currency
        ),
        reply_markup=natal_menu(settings.natal_reading_price, settings.author_currency),
    )


@router.callback_query(F.data == "natal:buy")
async def cb_buy(
    call: CallbackQuery, repo: Repo, settings: Settings, interpreter: Interpreter
) -> None:
    if not isinstance(call.message, Message):
        await call.answer()
        return

    paid = await repo.unspent_payment(call.from_user.id, PAYLOAD_NATAL)
    if paid is not None or free_for(settings, call.from_user.id):
        await call.answer()
        await write(call.message, repo, settings, interpreter, call.from_user.id)
        return

    await send_invoice(call.message, settings, PAYLOAD_NATAL)
    await call.answer()


@router.callback_query(F.data == "natal:write")
async def cb_write(
    call: CallbackQuery, repo: Repo, settings: Settings, interpreter: Interpreter
) -> None:
    await call.answer()
    if isinstance(call.message, Message):
        await write(call.message, repo, settings, interpreter, call.from_user.id)


async def write(
    message: Message,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
    user_id: int,
) -> None:
    card = await card_for(repo, user_id)
    if card is None or not card.has_houses:
        await message.answer(texts.NATAL_NEEDS_TIME, reply_markup=back_to_menu())
        return

    paid = await repo.unspent_payment(user_id, PAYLOAD_NATAL)
    if paid is None and not free_for(settings, user_id):
        await message.answer(
            texts.NATAL_OFFER.format(
                price=settings.natal_reading_price, currency=settings.author_currency
            ),
            reply_markup=natal_menu(settings.natal_reading_price, settings.author_currency),
        )
        return

    status = await message.answer(texts.NATAL_WRITING)
    user = await repo.get_user(user_id)

    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await interpreter.natal(
                "\n".join(card.lines(aspects_limit=20)), describe(user)
            )
    except (GrokError, TimeoutError):
        logger.warning("Разбор карты не написан: модель не ответила")
        await status.edit_text(texts.NATAL_FAILED)
        return

    # Списываем только теперь: текст есть, и он сохранён
    await repo.save_reading(
        user_id=user_id,
        spread_key="natal",
        question=None,
        cards=[],
        interpretation=result.text,
        ai_used=result.ai_used,
        mode=ReadingMode.NATAL,
    )
    if paid is not None:
        await repo.spend_payment(paid)

    await status.delete()
    for chunk in split_text(result.text):
        await message.answer(chunk)
    await message.answer(texts.NATAL_READY, reply_markup=back_to_menu())
