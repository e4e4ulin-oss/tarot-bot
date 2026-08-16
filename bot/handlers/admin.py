"""Кабинет автора: заявки, ответы клиентам, статистика."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..config import Settings
from ..db import Order, OrderStatus, Repo
from ..filters import IsAdmin
from ..keyboards import admin_order_menu, followup_menu
from ..states import AdminFlow
from ..utils import fmt_date

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _order_id(data: str) -> int:
    return int(data.rsplit(":", 1)[1])


@router.callback_query(F.data.startswith("adm:take:"))
async def cb_take(call: CallbackQuery, repo: Repo) -> None:
    order = await repo.get_order(_order_id(call.data))
    if order is None:
        await call.answer(texts.ERROR, show_alert=True)
        return

    await repo.set_order_status(order, OrderStatus.IN_PROGRESS, admin_id=call.from_user.id)
    await call.answer(texts.ADMIN_TAKEN.format(order_id=order.id, admin=call.from_user.full_name))
    if isinstance(call.message, Message):
        try:
            await call.message.edit_reply_markup(
                reply_markup=admin_order_menu(order.id, taken=True)
            )
        except TelegramBadRequest:  # pragma: no cover - разметка уже такая же
            pass


@router.callback_query(F.data.startswith("adm:answer:"))
async def cb_answer(call: CallbackQuery, state: FSMContext) -> None:
    order_id = _order_id(call.data)
    await state.set_state(AdminFlow.answering)
    await state.update_data(order_id=order_id)
    if isinstance(call.message, Message):
        await call.message.answer(texts.ADMIN_ANSWER_PROMPT.format(order_id=order_id))
    await call.answer()


@router.callback_query(F.data.startswith("adm:decline:"))
async def cb_decline(call: CallbackQuery, repo: Repo, settings: Settings, bot: Bot) -> None:
    order = await repo.get_order(_order_id(call.data))
    if order is None:
        await call.answer(texts.ERROR, show_alert=True)
        return

    await repo.set_order_status(order, OrderStatus.DECLINED, admin_id=call.from_user.id)
    await call.answer(texts.ADMIN_DECLINED.format(order_id=order.id))
    if isinstance(call.message, Message):
        await call.message.answer(texts.ADMIN_DECLINED.format(order_id=order.id))

    try:
        await bot.send_message(
            order.user_id,
            texts.AUTHOR_DECLINED.format(order_id=order.id, author=settings.author_name),
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось сообщить клиенту об отклонении №%s: %s", order.id, exc)


@router.message(AdminFlow.answering)
async def deliver_answer(
    message: Message,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    bot: Bot,
) -> None:
    data = await state.get_data()
    order: Order | None = await repo.get_order(int(data.get("order_id", 0)))
    if order is None:
        await state.clear()
        await message.answer(texts.ERROR)
        return

    header = texts.AUTHOR_ANSWER_HEADER.format(author=settings.author_name, order_id=order.id)
    try:
        await bot.send_message(order.user_id, header)
        await bot.copy_message(
            chat_id=order.user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await bot.send_message(
            order.user_id,
            "Если что-то осталось непонятным — можно задать уточняющий вопрос.",
            reply_markup=followup_menu(order.id),
        )
    except TelegramAPIError as exc:
        logger.warning("Доставка ответа по заявке №%s не удалась: %s", order.id, exc)
        await message.answer(
            texts.ADMIN_DELIVERY_FAILED.format(order_id=order.id, reason=texts.esc(str(exc)))
        )
        return

    await state.clear()
    await repo.set_order_status(order, OrderStatus.ANSWERED, admin_id=message.from_user.id)
    await message.answer(texts.ADMIN_ANSWER_SENT.format(order_id=order.id))


@router.message(Command("orders"))
async def cmd_orders(message: Message, repo: Repo) -> None:
    orders = await repo.open_orders()
    if not orders:
        await message.answer(texts.ADMIN_NO_ORDERS)
        return

    blocks = [texts.ADMIN_ORDERS_HEADER, ""]
    for order in orders:
        user = await repo.get_user(order.user_id)
        blocks.append(
            texts.ADMIN_ORDER_LINE.format(
                order_id=order.id,
                status=order.status_label,
                date=fmt_date(order.created_at),
                user=texts.esc(user.display if user else str(order.user_id)),
                question=texts.esc(texts.short(order.question, 140)),
            )
        )
        blocks.append("")
    await message.answer("\n".join(blocks).strip())

    for order in orders:
        await message.answer(
            f"Заявка №{order.id} — действия:",
            reply_markup=admin_order_menu(order.id, taken=order.status is OrderStatus.IN_PROGRESS),
        )


@router.message(Command("order"))
async def cmd_order(message: Message, repo: Repo) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /order &lt;номер заявки&gt;")
        return

    order = await repo.get_order(int(parts[1]))
    if order is None:
        await message.answer("Заявка не найдена.")
        return

    user = await repo.get_user(order.user_id)
    await message.answer(
        texts.ADMIN_NEW_ORDER.format(
            order_id=order.id,
            user=texts.esc(user.display if user else "—"),
            user_id=order.user_id,
            topic=texts.esc(texts.topic_label(order.topic)),
            contact=texts.esc(order.contact) or "—",
            question=texts.esc(order.question),
        )
        + f"\n\n<b>Статус:</b> {order.status_label}",
        reply_markup=admin_order_menu(order.id, taken=order.status is not OrderStatus.NEW),
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, repo: Repo) -> None:
    await message.answer(texts.ADMIN_STATS.format(**await repo.stats()))
