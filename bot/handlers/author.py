"""Авторский режим со стороны клиента: заявка уходит Анастасии, она отвечает лично."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..config import Settings
from ..db import Order, OrderStatus, Repo
from ..keyboards import (
    admin_order_menu,
    back_to_menu,
    cancel_order_menu,
    confirm_order_menu,
    contact_menu,
    main_menu,
    topics_menu,
)
from ..states import AuthorFlow
from ..utils import fmt_date

logger = logging.getLogger(__name__)

router = Router(name="author")


@router.callback_query(F.data == "menu:author")
async def cb_author(call: CallbackQuery, state: FSMContext, repo: Repo, settings: Settings) -> None:
    await state.clear()
    existing = await repo.open_order_for_user(call.from_user.id)
    if existing is not None:
        if isinstance(call.message, Message):
            await call.message.edit_text(
                texts.AUTHOR_ALREADY_OPEN.format(
                    order_id=existing.id, date=fmt_date(existing.created_at)
                ),
                reply_markup=cancel_order_menu(existing.id),
            )
        await call.answer()
        return

    intro = texts.AUTHOR_INTRO.format(author=settings.author_name)
    if settings.author_price_text:
        intro += texts.AUTHOR_PRICE.format(price=texts.esc(settings.author_price_text))
    intro += f"\n\n{texts.AUTHOR_ASK_TOPIC}"

    if isinstance(call.message, Message):
        await call.message.edit_text(intro, reply_markup=topics_menu())
    await call.answer()


@router.callback_query(F.data.startswith("topic:"))
async def cb_topic(call: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    topic = call.data.split(":", 1)[1]
    await state.set_state(AuthorFlow.waiting_question)
    await state.update_data(topic=topic)
    if isinstance(call.message, Message):
        await call.message.edit_text(
            f"<b>{texts.esc(texts.topic_label(topic))}</b>\n\n"
            "Опишите ситуацию и вопрос одним сообщением.\n\n"
            "/cancel — выйти."
        )
    await call.answer()


@router.message(AuthorFlow.waiting_question, F.text)
async def on_question(message: Message, state: FSMContext, settings: Settings) -> None:
    question = (message.text or "").strip()
    limit = settings.max_question_length * 3  # для авторского разбора допускаем длинный текст
    if len(question) > limit:
        await message.answer(texts.QUESTION_TOO_LONG.format(limit=limit))
        return

    await state.update_data(question=question)
    await state.set_state(AuthorFlow.waiting_contact)
    await message.answer(texts.AUTHOR_ASK_CONTACT, reply_markup=contact_menu())


@router.message(AuthorFlow.waiting_question)
async def on_non_text_question(message: Message) -> None:
    await message.answer(texts.QUESTION_REQUIRED)


@router.message(AuthorFlow.waiting_contact, F.text)
async def on_contact(message: Message, state: FSMContext) -> None:
    await state.update_data(contact=(message.text or "").strip()[:128])
    await _show_confirm(message, state)


@router.callback_query(AuthorFlow.waiting_contact, F.data == "order:nocontact")
async def cb_no_contact(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(contact=None)
    if isinstance(call.message, Message):
        await _show_confirm(call.message, state)
    await call.answer()


async def _show_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AuthorFlow.confirming)
    await message.answer(
        texts.AUTHOR_CONFIRM.format(
            topic=texts.esc(texts.topic_label(data.get("topic"))),
            question=texts.esc(data.get("question", "")),
            contact=texts.esc(data.get("contact")) or "не указан",
        ),
        reply_markup=confirm_order_menu(),
    )


@router.callback_query(AuthorFlow.confirming, F.data == "order:submit")
async def cb_submit(
    call: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    bot: Bot,
) -> None:
    data = await state.get_data()
    question = data.get("question")
    if not question:
        await call.answer(texts.ERROR, show_alert=True)
        await state.clear()
        return

    existing = await repo.open_order_for_user(call.from_user.id)
    if existing is not None:
        await state.clear()
        if isinstance(call.message, Message):
            await call.message.edit_text(
                texts.AUTHOR_ALREADY_OPEN.format(
                    order_id=existing.id, date=fmt_date(existing.created_at)
                ),
                reply_markup=cancel_order_menu(existing.id),
            )
        await call.answer()
        return

    order = await repo.create_order(
        user_id=call.from_user.id,
        question=question,
        topic=data.get("topic"),
        contact=data.get("contact"),
    )
    await state.clear()

    await notify_admin_new_order(
        bot, settings, repo, order, call.from_user.full_name, call.from_user.username
    )

    if isinstance(call.message, Message):
        await call.message.edit_text(
            texts.AUTHOR_SENT.format(order_id=order.id, author=settings.author_name),
            reply_markup=back_to_menu(),
        )
    await call.answer()


async def notify_admin_new_order(
    bot: Bot,
    settings: Settings,
    repo: Repo,
    order: Order,
    full_name: str | None,
    username: str | None,
) -> None:
    if not settings.admin_chat_id:
        logger.warning("ADMIN_CHAT_ID не задан — заявка №%s никуда не отправлена", order.id)
        return

    user_line = texts.esc(full_name or "без имени")
    if username:
        user_line += f" (@{texts.esc(username)})"

    try:
        sent = await bot.send_message(
            settings.admin_chat_id,
            texts.ADMIN_NEW_ORDER.format(
                order_id=order.id,
                user=user_line,
                user_id=order.user_id,
                topic=texts.esc(texts.topic_label(order.topic)),
                contact=texts.esc(order.contact) or "—",
                question=texts.esc(order.question),
            ),
            reply_markup=admin_order_menu(order.id),
        )
        await repo.set_admin_message(order, sent.message_id)
    except TelegramAPIError:
        logger.exception("Не удалось отправить заявку №%s в админ-чат", order.id)


@router.callback_query(F.data.startswith("order:cancel:"))
async def cb_cancel_order(call: CallbackQuery, repo: Repo, settings: Settings, bot: Bot) -> None:
    order_id = int(call.data.rsplit(":", 1)[1])
    order = await repo.get_order(order_id)
    if order is None or order.user_id != call.from_user.id or not order.is_open:
        await call.answer(texts.ERROR, show_alert=True)
        return

    await repo.set_order_status(order, OrderStatus.CANCELLED)
    if isinstance(call.message, Message):
        await call.message.edit_text(
            texts.AUTHOR_CANCELLED.format(order_id=order.id),
            reply_markup=main_menu(settings.author_name),
        )
    await call.answer()

    if settings.admin_chat_id:
        try:
            await bot.send_message(
                settings.admin_chat_id,
                f"↩️ Клиент отменил заявку №{order.id}.",
            )
        except TelegramAPIError:  # pragma: no cover
            logger.exception("Не удалось уведомить админ-чат об отмене №%s", order.id)


@router.callback_query(F.data.startswith("order:followup:"))
async def cb_followup(call: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    order_id = int(call.data.rsplit(":", 1)[1])
    await state.set_state(AuthorFlow.waiting_followup)
    await state.update_data(order_id=order_id)
    if isinstance(call.message, Message):
        await call.message.answer(texts.AUTHOR_FOLLOWUP_ASK.format(author=settings.author_name))
    await call.answer()


@router.message(AuthorFlow.waiting_followup, F.text)
async def on_followup(
    message: Message, state: FSMContext, repo: Repo, settings: Settings, bot: Bot
) -> None:
    data = await state.get_data()
    order = await repo.get_order(int(data.get("order_id", 0)))
    await state.clear()

    if order is None or order.user_id != message.from_user.id:
        await message.answer(texts.ERROR)
        return

    if settings.admin_chat_id:
        user_line = texts.esc(message.from_user.full_name)
        if message.from_user.username:
            user_line += f" (@{texts.esc(message.from_user.username)})"
        try:
            await bot.send_message(
                settings.admin_chat_id,
                texts.ADMIN_FOLLOWUP.format(
                    order_id=order.id,
                    user=user_line,
                    text=texts.esc(message.text or ""),
                ),
                reply_markup=admin_order_menu(order.id, taken=True),
            )
        except TelegramAPIError:  # pragma: no cover
            logger.exception("Не удалось передать уточнение по заявке №%s", order.id)
            await message.answer(texts.ERROR)
            return

    await message.answer(
        texts.AUTHOR_FOLLOWUP_SENT.format(author=settings.author_name),
        reply_markup=back_to_menu(),
    )
