"""Автоматический режим: бот тасует колоду и сам пишет разбор."""

from __future__ import annotations

import logging
import secrets

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from .. import analytics, texts
from ..config import Settings
from ..db import Repo
from ..keyboards import (
    after_reading_menu,
    number_menu,
    paywall_menu,
    question_menu,
    spreads_menu,
    subscribe_menu,
)
from ..services import reading
from ..services.cards import send_card_images
from ..services.interpreter import DISCLAIMER, Interpreter
from ..states import AutoFlow
from ..tarot import (
    SPREADS,
    Spread,
    format_cards,
    get_spread,
)
from ..utils import split_text
from .invite import tell_the_inviter

logger = logging.getLogger(__name__)

# Числа крупнее не нужны: это жест, а не счёт
NUMBER_LIMIT = 99

router = Router(name="auto")


@router.callback_query(F.data == "menu:auto")
async def cb_spreads(call: CallbackQuery, state: FSMContext, repo: Repo) -> None:
    await state.clear()
    await repo.log_event(call.from_user.id, analytics.AUTO_OPEN)
    if isinstance(call.message, Message):
        await call.message.edit_text(texts.CHOOSE_SPREAD, reply_markup=spreads_menu())
    await call.answer()


@router.callback_query(F.data.startswith("spread:"))
async def cb_spread_chosen(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    spread = SPREADS.get(key)
    if spread is None:
        await call.answer(texts.ERROR, show_alert=True)
        return

    await state.set_state(AutoFlow.waiting_question)
    await state.update_data(spread_key=key)

    template = texts.ASK_QUESTION_YES_NO if spread.yes_no else texts.ASK_QUESTION
    if isinstance(call.message, Message):
        await call.message.edit_text(
            template.format(spread=spread.title, subtitle=spread.subtitle),
            reply_markup=question_menu(key, allow_skip=not spread.yes_no),
        )
    await call.answer()


@router.callback_query(F.data.startswith("draw:"))
async def cb_draw_without_question(
    call: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    key = call.data.split(":", 1)[1]
    spread = SPREADS.get(key)
    if spread is None:
        await call.answer(texts.ERROR, show_alert=True)
        return
    await state.set_state(AutoFlow.waiting_number)
    await state.update_data(spread_key=key, question=None)
    await call.answer()
    if isinstance(call.message, Message):
        await call.message.answer(texts.ASK_NUMBER, reply_markup=number_menu(key))


@router.message(AutoFlow.waiting_question, F.text)
async def on_question(
    message: Message,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    data = await state.get_data()
    spread = get_spread(data.get("spread_key", "ptf"))
    question = (message.text or "").strip()

    if len(question) > settings.max_question_length:
        await message.answer(texts.QUESTION_TOO_LONG.format(limit=settings.max_question_length))
        return

    await state.set_state(AutoFlow.waiting_number)
    await state.update_data(spread_key=spread.key, question=question)
    await message.answer(texts.ASK_NUMBER, reply_markup=number_menu(spread.key))


@router.message(AutoFlow.waiting_question)
async def on_non_text_question(message: Message) -> None:
    await message.answer(texts.QUESTION_REQUIRED)


@router.message(AutoFlow.waiting_number, F.text)
async def on_number(
    message: Message,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= NUMBER_LIMIT:
        await message.answer(texts.BAD_NUMBER)
        return

    data = await state.get_data()
    spread = get_spread(data.get("spread_key", "ptf"))
    await state.clear()
    await run_reading(
        message,
        message.from_user.id,
        spread,
        data.get("question"),
        repo,
        settings,
        interpreter,
        number=int(raw),
    )


@router.callback_query(AutoFlow.waiting_number, F.data.startswith("number:"))
async def cb_number_from_the_deck(
    call: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    """Человек может не называть число — тогда его называет колода."""
    data = await state.get_data()
    spread = get_spread(data.get("spread_key", "ptf"))
    question = data.get("question")
    await state.clear()
    await call.answer()
    if isinstance(call.message, Message):
        await run_reading(
            call.message,
            call.from_user.id,
            spread,
            question,
            repo,
            settings,
            interpreter,
            number=secrets.randbelow(NUMBER_LIMIT) + 1,
        )


async def refuse(message: Message, settings: Settings, decision: reading.Refused) -> None:
    """Говорит, почему расклада не будет, и предлагает выход."""
    if decision.reason == "needs_daily":
        await message.answer(texts.NEEDS_DAILY, reply_markup=subscribe_menu())
    elif decision.reason == "payment_not_ready":
        await message.answer(texts.PAYMENT_NOT_READY.format(author=settings.author_name))
    elif decision.reason == "daily_limit":
        await message.answer(
            texts.DAILY_LIMIT.format(limit=decision.limit, author=settings.author_name),
            reply_markup=after_reading_menu(settings.author_name),
        )
    else:
        await message.answer(
            texts.FREE_SPENT.format(
                total=settings.free_auto_readings,
                price=settings.auto_reading_price,
                currency=settings.author_currency,
            ),
            reply_markup=paywall_menu(settings.auto_reading_price, settings.author_currency),
        )


async def run_reading(
    message: Message,
    user_id: int,
    spread: Spread,
    question: str | None,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
    *,
    number: int,
) -> None:
    """Общий сценарий: доступ → лимит → карты → разбор → сохранение.

    Сами правила живут в services/reading: из мини-приложения расклад идёт по
    ним же, и двух разных ответов на вопрос «можно ли» быть не должно.
    """
    decision = await reading.start(repo, settings, user_id, spread, question, number=number)
    if isinstance(decision, reading.Refused):
        await refuse(message, settings, decision)
        return

    drawn = decision.drawn
    await send_card_images(message.bot, message.chat.id, drawn, repo, settings)
    cards_message = await message.answer(format_cards(spread, drawn, question))
    status = await message.answer(texts.INTERPRETING)

    # «печатает…» в чате, пока модель думает — иначе кажется, что бот завис
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        done = await reading.finish(
            repo, interpreter, user_id, spread, question, decision, settings
        )
    result = done.interpretation

    try:
        await status.delete()
    except TelegramBadRequest:  # pragma: no cover - сообщение могли удалить вручную
        logger.debug("Не удалось удалить служебное сообщение")

    body = result.text if result.ai_used else f"{texts.FALLBACK_NOTE}\n\n{result.text}"
    chunks = split_text(body)
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        await cards_message.answer(
            chunk + (f"\n\n<i>{DISCLAIMER}</i>" if is_last else ""),
            reply_markup=after_reading_menu(settings.author_name) if is_last else None,
        )

    # Начислено внутри расклада; здесь остаётся только сказать об этом
    if done.rewarded is not None and done.rewarded[1]:
        await tell_the_inviter(message, *done.rewarded)

