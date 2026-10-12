"""Кабинет автора: заявки, ответы клиентам, статистика."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..config import Settings
from ..db import Order, OrderStatus, Repo
from ..filters import IsAdmin
from ..keyboards import admin_order_menu, followup_menu
from ..services import UPDATE_APPLIED, GrokError, Interpreter, ServerOps
from ..services.server import LOGS_DEFAULT, LOGS_MAX, LOGS_MIN
from ..states import AdminFlow
from ..tiers import tier_label
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
async def cmd_orders(message: Message, repo: Repo, settings: Settings) -> None:
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
                tier=texts.esc(tier_label(settings, order.tier)),
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
async def cmd_order(message: Message, repo: Repo, settings: Settings) -> None:
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
            tier=texts.esc(tier_label(settings, order.tier)),
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


@router.message(Command("diag"))
async def cmd_diag(message: Message, settings: Settings, interpreter: Interpreter) -> None:
    """Проверка настроек и живой запрос к модели — чтобы не искать причину в логах хостинга."""
    lines = [
        "<b>Диагностика</b>",
        "",
        f"Админ-чат: {'задан' if settings.admin_chat_id else '❌ не задан (ADMIN_CHAT_ID)'}",
        f"Кто отвечает на заявки: {settings.admin_ids or '❌ не задано (ADMIN_IDS)'}",
        f"Лимит авто-раскладов в сутки: {settings.daily_auto_limit or 'без лимита'}",
        "",
        f"<b>Разбор от ИИ</b>\nМодель: <code>{texts.esc(settings.ai_model)}</code>",
        f"Адрес: <code>{texts.esc(settings.ai_base_url)}</code>",
    ]

    if not interpreter.grok.enabled:
        lines.append(
            "Ключ: ❌ не задан.\nДобавьте переменную <code>AI_API_KEY</code> "
            "и перезапустите бота — до тех пор разбор идёт по базе значений карт."
        )
        await message.answer("\n".join(lines))
        return

    lines.append("Ключ: задан, проверяю ответ…")
    status = await message.answer("\n".join(lines))

    try:
        reply = await interpreter.grok.complete(
            # с запасом: рассуждающие модели часть лимита тратят на размышления
            "Отвечай одним словом.",
            "Скажи: готово",
            max_tokens=200,
        )
        lines[-1] = f"Ключ: ✅ работает (ответ модели: «{texts.esc(reply[:40])}»)"
    except GrokError as exc:
        lines[-1] = (
            f"Ключ: ❌ модель не отвечает.\n<code>{texts.esc(str(exc)[:300])}</code>\n\n"
            "Частые причины: неверный ключ, нулевой баланс у провайдера, "
            "недоступная модель или блокировка по региону."
        )

    await status.edit_text("\n".join(lines))


# Telegram не принимает сообщения длиннее 4096 символов, а в журнале интересен
# хвост — обрезаем начало.
_OUTPUT_LIMIT = 3400


def _tail(output: str) -> str:
    clipped = output[-_OUTPUT_LIMIT:]
    if len(output) > _OUTPUT_LIMIT:
        clipped = "…\n" + clipped.split("\n", 1)[-1]
    return texts.esc(clipped or "—")


@router.message(Command("logs"))
async def cmd_logs(message: Message, command: CommandObject, server: ServerOps) -> None:
    """Последние строки журнала службы — чтобы смотреть логи с телефона."""
    lines = LOGS_DEFAULT
    if command.args:
        asked = command.args.strip()
        if not asked.isdigit():
            await message.answer(
                texts.SERVER_LOGS_USAGE.format(low=LOGS_MIN, high=LOGS_MAX, default=LOGS_DEFAULT)
            )
            return
        lines = int(asked)

    result = await server.logs(lines)
    if result.unavailable or not result.ok:
        await message.answer(texts.SERVER_UNAVAILABLE.format(output=_tail(result.output)))
        return
    if not result.output:
        await message.answer(texts.SERVER_LOGS_EMPTY)
        return

    shown = min(max(lines, LOGS_MIN), LOGS_MAX)
    await message.answer(texts.SERVER_LOGS_HEADER.format(lines=shown, output=_tail(result.output)))


@router.message(Command("update"))
async def cmd_update(message: Message, server: ServerOps) -> None:
    """Подтянуть правки из GitHub не дожидаясь таймера."""
    status = await message.answer(texts.SERVER_UPDATE_CHECKING)
    result = await server.update()
    output = _tail(result.output)

    if result.unavailable:
        await status.edit_text(texts.SERVER_UNAVAILABLE.format(output=output))
        return
    if result.code == UPDATE_APPLIED:
        await status.edit_text(texts.SERVER_UPDATE_APPLIED.format(output=output))
        # Ответ уже отправлен, так что переживать за прерванный перезапуск нечего.
        await server.restart()
        return
    if result.ok:
        await status.edit_text(texts.SERVER_UPDATE_FRESH.format(output=output))
        return

    await status.edit_text(texts.SERVER_UPDATE_FAILED.format(output=output))


@router.message(Command("restart"))
async def cmd_restart(message: Message, server: ServerOps) -> None:
    """Перезапуск службы: например, после правки .env на сервере."""
    await message.answer(texts.SERVER_RESTARTING)
    result = await server.restart()
    if not result.ok:
        await message.answer(texts.SERVER_UNAVAILABLE.format(output=_tail(result.output)))
