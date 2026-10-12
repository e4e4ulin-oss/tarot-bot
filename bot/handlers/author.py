"""Авторский режим со стороны клиента: заявка уходит автору, он отвечает лично."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import analytics, texts
from ..config import Settings
from ..db import Order, OrderStatus, Repo
from ..keyboards import (
    admin_order_menu,
    back_to_menu,
    cancel_order_menu,
    confirm_order_menu,
    contact_menu,
    followup_menu,
    main_menu,
    tiers_menu,
    topics_menu,
)
from ..schedule import (
    format_moment,
    is_working_time,
    now,
    start_of_work,
    work_hours_label,
)
from ..states import AuthorFlow
from ..tiers import Tier, find_tier, followups_allowed, tier_label, tiers
from ..utils import fmt_date

logger = logging.getLogger(__name__)

router = Router(name="author")


@router.callback_query(F.data == "menu:author")
async def cb_author(call: CallbackQuery, state: FSMContext, repo: Repo, settings: Settings) -> None:
    await state.clear()
    await repo.log_event(call.from_user.id, analytics.AUTHOR_OPEN)
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

    available = tiers(settings)
    intro = texts.AUTHOR_INTRO.format(author=settings.author_name)
    if available:
        intro += "\n\n" + "\n\n".join(_tier_block(tier, settings) for tier in available)
    if settings.author_price_text:
        intro += texts.AUTHOR_PRICE.format(price=texts.esc(settings.author_price_text))

    # Уровней может не быть вовсе: тогда заявка оформляется как раньше, без выбора.
    if available:
        intro += f"\n\n{texts.AUTHOR_ASK_TIER}"
        markup = tiers_menu(available, settings.author_currency)
    else:
        intro += f"\n\n{texts.AUTHOR_ASK_TOPIC}"
        markup = topics_menu()

    if isinstance(call.message, Message):
        await call.message.edit_text(intro, reply_markup=markup)
    await call.answer()


def _tier_block(tier: Tier, settings: Settings) -> str:
    return texts.AUTHOR_TIER_BLOCK.format(
        title=texts.esc(tier.title),
        price=texts.esc(tier.price_label(settings.author_currency)),
        includes="\n".join(f"• {texts.esc(line)}" for line in tier.includes),
    )


@router.callback_query(F.data.startswith("tier:"))
async def cb_tier(
    call: CallbackQuery, state: FSMContext, settings: Settings, repo: Repo
) -> None:
    tier = find_tier(settings, call.data.split(":", 1)[1])
    if tier is None:
        await call.answer(texts.ERROR, show_alert=True)
        return

    await state.update_data(tier=tier.key)
    await repo.log_event(call.from_user.id, analytics.TIER_CHOSEN, tier.key)
    chosen = texts.AUTHOR_TIER_CHOSEN.format(
        title=texts.esc(tier.title),
        price=texts.esc(tier.price_label(settings.author_currency)),
        includes="\n".join(f"• {texts.esc(line)}" for line in tier.includes),
        deadline=texts.genitive_hours(tier.hours),
    )
    chosen += _work_hours_note(settings) + "\n\n"
    if isinstance(call.message, Message):
        await call.message.edit_text(chosen + texts.AUTHOR_ASK_TOPIC, reply_markup=topics_menu())
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
async def on_contact(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.update_data(contact=(message.text or "").strip()[:128])
    await _show_confirm(message, state, settings)


@router.callback_query(AuthorFlow.waiting_contact, F.data == "order:nocontact")
async def cb_no_contact(call: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.update_data(contact=None)
    if isinstance(call.message, Message):
        await _show_confirm(call.message, state, settings)
    await call.answer()


async def _show_confirm(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    await state.set_state(AuthorFlow.confirming)
    await message.answer(
        texts.AUTHOR_CONFIRM.format(
            tier=texts.esc(tier_label(settings, data.get("tier"))),
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
        tier=data.get("tier"),
    )
    await state.clear()
    await repo.log_event(call.from_user.id, analytics.ORDER_SENT, order.tier)

    await notify_admin_new_order(
        bot, settings, repo, order, call.from_user.full_name, call.from_user.username
    )

    tier = find_tier(settings, order.tier)
    if tier is None:
        sent = texts.AUTHOR_SENT_NO_TIER.format(
            order_id=order.id, author=settings.author_name
        )
    else:
        sent = texts.AUTHOR_SENT.format(
            order_id=order.id,
            author=settings.author_name,
            deadline=texts.genitive_hours(tier.hours),
            work_hours=_work_hours_note(settings),
        )

    if isinstance(call.message, Message):
        await call.message.edit_text(sent, reply_markup=back_to_menu())
    await call.answer()


async def notify_admins(
    bot: Bot, settings: Settings, text: str, reply_markup: object | None = None
) -> Message | None:
    """Шлёт сообщение всем, кто ведёт заявки. Возвращает первое доставленное.

    Кто-то мог не запустить бота или заблокировать его — это не повод терять заявку
    для остальных, поэтому каждая отправка отдельная.
    """
    if not settings.notify_chat_ids:
        logger.warning("Некому отправлять: не заданы ADMIN_CHAT_ID и ADMIN_IDS")
        return None

    delivered: Message | None = None
    for chat_id in settings.notify_chat_ids:
        try:
            sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
            delivered = delivered or sent
        except TelegramAPIError as exc:
            logger.warning("Не доставлено в чат %s: %s", chat_id, exc)
    return delivered


async def notify_admin_new_order(
    bot: Bot,
    settings: Settings,
    repo: Repo,
    order: Order,
    full_name: str | None,
    username: str | None,
) -> None:
    user_line = texts.esc(full_name or "без имени")
    if username:
        user_line += f" (@{texts.esc(username)})"

    sent = await notify_admins(
        bot,
        settings,
        texts.ADMIN_NEW_ORDER.format(
            order_id=order.id,
            tier=texts.esc(tier_label(settings, order.tier)),
            user=user_line,
            user_id=order.user_id,
            topic=texts.esc(texts.topic_label(order.topic)),
            contact=texts.esc(order.contact) or "—",
            question=texts.esc(order.question),
        ),
        reply_markup=admin_order_menu(order.id),
    )
    if sent is not None:
        await repo.set_admin_message(order, sent.message_id)


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

    await notify_admins(bot, settings, f"↩️ Клиент отменил заявку №{order.id}.")


@router.callback_query(F.data.startswith("order:followup:"))
async def cb_followup(
    call: CallbackQuery, state: FSMContext, settings: Settings, repo: Repo
) -> None:
    order = await repo.get_order(int(call.data.rsplit(":", 1)[1]))
    if order is None or order.user_id != call.from_user.id:
        await call.answer(texts.ERROR, show_alert=True)
        return

    # Кнопка живёт в старом сообщении и доступна повторно, поэтому лимит
    # проверяется здесь, а не только при её показе.
    if _followups_left(settings, order) <= 0:
        await _say_no_followups_left(call.message, settings, order)
        await call.answer()
        return

    await state.set_state(AuthorFlow.waiting_followup)
    await state.update_data(order_id=order.id)
    if isinstance(call.message, Message):
        await call.message.answer(texts.AUTHOR_FOLLOWUP_ASK.format(author=settings.author_name))
    await call.answer()


def _followups_left(settings: Settings, order: Order) -> int:
    return followups_allowed(settings, order.tier) - (order.followups_used or 0)


async def _say_no_followups_left(message: object, settings: Settings, order: Order) -> None:
    if not isinstance(message, Message):
        return
    allowed = followups_allowed(settings, order.tier)
    tier = find_tier(settings, order.tier)
    await message.answer(
        texts.AUTHOR_FOLLOWUP_SPENT.format(
            tier=texts.esc(tier.title if tier else "—"),
            allowed=texts.plural_followups(allowed),
        ),
        reply_markup=back_to_menu(),
    )


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

    if _followups_left(settings, order) <= 0:
        await _say_no_followups_left(message, settings, order)
        return

    user_line = texts.esc(message.from_user.full_name)
    if message.from_user.username:
        user_line += f" (@{texts.esc(message.from_user.username)})"
    await notify_admins(
        bot,
        settings,
        texts.ADMIN_FOLLOWUP.format(
            order_id=order.id, user=user_line, text=texts.esc(message.text or "")
        ),
        reply_markup=admin_order_menu(order.id, taken=True),
    )

    await repo.count_followup(order)

    left = _followups_left(settings, order)
    sent = texts.AUTHOR_FOLLOWUP_SENT.format(author=settings.author_name)
    if left > 0:
        sent += "\n\n" + texts.AUTHOR_FOLLOWUP_LEFT.format(left=texts.plural_followups(left))
        markup = followup_menu(order.id)
    else:
        markup = back_to_menu()
    await message.answer(sent, reply_markup=markup)


def _work_hours_note(settings: Settings) -> str:
    """Рабочие часы, а в нерабочее время — ещё и когда начнётся отсчёт."""
    note = texts.AUTHOR_WORK_HOURS.format(
        hours=work_hours_label(settings), tz=settings.work_timezone_label
    )
    moment = now(settings)
    if not is_working_time(settings, moment):
        note += texts.AUTHOR_OFF_HOURS.format(
            when=format_moment(settings, start_of_work(settings, moment), moment)
        )
    return note
