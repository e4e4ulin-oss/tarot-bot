"""Натальная карта и прогноз по транзитам.

Расчёт и текст здесь разного происхождения, и это видно в ответе: карта и
транзиты считаются программой по эфемеридам, а связный текст пишет модель.
Если модель недоступна, расчёт всё равно показывается — он от неё не зависит.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..db import Repo
from ..keyboards import back_to_menu
from ..natal import Full, forecast, full, timeline
from ..profile import describe, stored_place
from ..services import GrokError, Interpreter

logger = logging.getLogger(__name__)

router = Router(name="chart")


async def card_for(repo: Repo, user_id: int) -> Full | None:
    user = await repo.get_user(user_id)
    if user is None or user.birth_date is None:
        return None
    return full(user.birth_date, user.birth_time, user.birth_city, place=stored_place(user))


@router.message(Command("chart"))
async def cmd_chart(message: Message, repo: Repo) -> None:
    await send_chart(message, repo, message.chat.id)


@router.callback_query(F.data == "chart:show")
async def cb_chart(call: CallbackQuery, repo: Repo) -> None:
    if isinstance(call.message, Message):
        await send_chart(call.message, repo, call.from_user.id)
    await call.answer()


async def send_chart(message: Message, repo: Repo, user_id: int) -> None:
    card = await card_for(repo, user_id)
    if card is None:
        await message.answer(texts.CHART_NEEDS_DATE, reply_markup=back_to_menu())
        return

    body = "\n".join(card.lines())
    note = texts.CHART_HOUSES_NOTE if card.has_houses else ""
    await message.answer(
        texts.CHART_TITLE.format(body=body) + (f"\n\n{note}" if note else ""),
        reply_markup=back_to_menu(),
    )


@router.message(Command("forecast"))
async def cmd_forecast(message: Message, repo: Repo, interpreter: Interpreter) -> None:
    await send_forecast(message, repo, interpreter, message.chat.id)


@router.callback_query(F.data == "chart:forecast")
async def cb_forecast(call: CallbackQuery, repo: Repo, interpreter: Interpreter) -> None:
    if isinstance(call.message, Message):
        await send_forecast(call.message, repo, interpreter, call.from_user.id)
    await call.answer()


async def send_forecast(
    message: Message, repo: Repo, interpreter: Interpreter, user_id: int
) -> None:
    card = await card_for(repo, user_id)
    if card is None:
        await message.answer(texts.CHART_NEEDS_DATE, reply_markup=back_to_menu())
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    moving = forecast(card, now)
    if not moving:
        await message.answer(texts.FORECAST_QUIET, reply_markup=back_to_menu())
        return

    # К каждому транзиту — срок: иначе «сейчас квадрат» читается одинаково
    # и для недели, и для полугода
    body = "\n".join(
        f"{transit}\n   {timeline(transit, card, now)}" for transit in moving
    )
    user = await repo.get_user(user_id)

    try:
        written = await interpreter.forecast(
            "\n".join(card.lines(aspects_limit=6)), body, describe(user)
        )
        text = written.text
    except (GrokError, TimeoutError):
        logger.warning("Прогноз без текста: модель недоступна")
        text = texts.FORECAST_UNAVAILABLE

    await message.answer(
        texts.FORECAST_TITLE.format(body=body, text=text), reply_markup=back_to_menu()
    )
