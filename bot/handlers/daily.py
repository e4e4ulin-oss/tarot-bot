"""Карта дня со стороны клиента: открыть и подписаться на утреннюю рассылку."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import analytics, texts
from ..config import Settings
from ..db import Repo
from ..keyboards import daily_card_menu
from ..schedule import now
from ..services.daily import card_message, card_of_the_day
from .auto import send_card_images

router = Router(name="daily")


async def show_card(
    message: Message, user_id: int, repo: Repo, settings: Settings, *, edit: bool = False
) -> None:
    day = now(settings).date()
    existing = await repo.daily_card(user_id, day)
    drawn = await card_of_the_day(repo, settings, user_id, day)
    subscribed = await repo.is_daily_subscribed(user_id)

    text = card_message(drawn)
    if existing is not None:
        text += texts.DAILY_ALREADY_TODAY
    if not subscribed:
        text += texts.DAILY_SUBSCRIBE_HINT

    markup = daily_card_menu(subscribed=subscribed)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)

    await send_card_images(message.bot, message.chat.id, [drawn], repo, settings)


@router.callback_query(F.data == "daily:open")
async def cb_daily(call: CallbackQuery, state: FSMContext, repo: Repo, settings: Settings) -> None:
    await state.clear()
    await repo.log_event(call.from_user.id, analytics.DAILY_OPEN)
    if isinstance(call.message, Message):
        await show_card(call.message, call.from_user.id, repo, settings, edit=True)
    await call.answer()


@router.message(Command("daily"))
async def cmd_daily(message: Message, state: FSMContext, repo: Repo, settings: Settings) -> None:
    await state.clear()
    if message.from_user is None:
        return
    await repo.log_event(message.from_user.id, analytics.DAILY_OPEN)
    await show_card(message, message.from_user.id, repo, settings)


@router.callback_query(F.data.in_({"daily:on", "daily:off"}))
async def cb_subscription(call: CallbackQuery, repo: Repo, settings: Settings) -> None:
    subscribe = call.data == "daily:on"
    await repo.set_daily_subscription(call.from_user.id, subscribe)

    if subscribe:
        await repo.log_event(call.from_user.id, analytics.DAILY_SUBSCRIBED)
        answer = texts.DAILY_SUBSCRIBED.format(
            hour=settings.daily_card_hour, tz=settings.work_timezone_label
        )
    else:
        answer = texts.DAILY_UNSUBSCRIBED

    if isinstance(call.message, Message):
        await call.message.answer(answer, reply_markup=daily_card_menu(subscribed=subscribe))
    await call.answer()
