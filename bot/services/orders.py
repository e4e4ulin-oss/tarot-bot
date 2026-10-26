"""Что происходит с заявкой, когда на неё ответили.

Ответить таролог может из переписки и из приложения. Отправка там разная —
в чате он пишет обычное сообщение и бот его копирует, в приложении набирает
текст в поле, — а вот последствия обязаны быть одни: ответ записан, заявка
отвечена, человеку сказано, сколько осталось уточнений.

Поэтому последствия живут здесь. Два места, которые считают по-своему, рано
или поздно разойдутся, и разойдутся они в счёте уточнений — то есть в деньгах.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .. import analytics, texts
from ..config import Settings
from ..db import Repo
from ..db.models import Order, OrderStatus
from ..keyboards import back_to_menu, followup_menu
from ..tiers import followups_allowed

logger = logging.getLogger(__name__)


def followups_left(settings: Settings, order: Order) -> int:
    return max(0, followups_allowed(settings, order.tier) - (order.followups_used or 0))


async def remember(repo: Repo, order: Order, text: str, admin_id: int | None) -> None:
    """Записывает ответ и переводит заявку в «отвечена».

    Запись идёт первой: отвеченная заявка, у которой нет ответа, — это ложь,
    и видно её будет только когда человек откроет приложение.
    """
    await repo.add_order_message(order.id, "author", text)
    await repo.set_order_status(order, OrderStatus.ANSWERED, admin_id=admin_id)
    await repo.log_event(order.user_id, analytics.ORDER_ANSWERED, order.tier)


async def tell_about_followups(bot: Bot, settings: Settings, order: Order) -> None:
    """Говорит человеку, сколько уточнений у него осталось."""
    left = followups_left(settings, order)
    if left > 0:
        await bot.send_message(
            order.user_id,
            texts.AUTHOR_FOLLOWUP_LEFT.format(left=texts.plural_followups(left)),
            reply_markup=followup_menu(order.id),
        )
    else:
        await bot.send_message(
            order.user_id, texts.AUTHOR_FOLLOWUP_NONE, reply_markup=back_to_menu()
        )


async def deliver(
    bot: Bot, settings: Settings, order: Order, text: str
) -> bool:
    """Шлёт человеку готовый ответ в чат. Возвращает, дошло ли.

    Приложение закрыто ровно тогда, когда ответ приходит, — значит узнать о
    нём человек может только из переписки.
    """
    try:
        await bot.send_message(
            order.user_id,
            texts.AUTHOR_ANSWER_HEADER.format(author=settings.author_name, order_id=order.id),
        )
        await bot.send_message(order.user_id, text)
        await tell_about_followups(bot, settings, order)
    except TelegramAPIError as exc:
        logger.warning("Ответ по заявке №%s не доставлен: %s", order.id, exc)
        return False
    return True


async def decline(bot: Bot, settings: Settings, repo: Repo, order: Order, admin_id: int) -> None:
    """Отклоняет заявку и сообщает об этом человеку."""
    await repo.set_order_status(order, OrderStatus.DECLINED, admin_id=admin_id)
    try:
        await bot.send_message(
            order.user_id,
            texts.AUTHOR_DECLINED.format(order_id=order.id, author=settings.author_name),
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось сообщить клиенту об отклонении №%s: %s", order.id, exc)
