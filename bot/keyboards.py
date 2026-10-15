"""Инлайн-клавиатуры."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .tarot import SPREAD_ORDER, SPREADS
from .texts import TOPICS
from .tiers import Tier


def main_menu(author_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🌅 Карта дня", callback_data="daily:open")
    builder.button(text="🔮 Авто-расклад", callback_data="menu:auto")
    builder.button(text="✍️ Авторский разбор", callback_data="menu:author")
    builder.button(text="📜 Мои расклады", callback_data="menu:history")
    builder.button(text="👤 Личный кабинет", callback_data="menu:profile")
    builder.button(text="ℹ️ О раскладах", callback_data="menu:about")
    builder.adjust(1)
    return builder.as_markup()


def spreads_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in SPREAD_ORDER:
        spread = SPREADS[key]
        suffix = "" if spread.size == 1 else f" · {spread.size} карт"
        builder.button(text=f"{spread.title}{suffix}", callback_data=f"spread:{key}")
    builder.button(text="‹ Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def question_menu(spread_key: str, *, allow_skip: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if allow_skip:
        builder.button(text="Без вопроса", callback_data=f"draw:{spread_key}")
    builder.button(text="‹ К раскладкам", callback_data="menu:auto")
    builder.adjust(1)
    return builder.as_markup()


def after_reading_menu(author_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔮 Ещё расклад", callback_data="menu:auto")
    builder.button(text="✍️ Авторский разбор", callback_data="menu:author")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def daily_card_menu(*, subscribed: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if subscribed:
        builder.button(text="🔕 Не присылать каждое утро", callback_data="daily:off")
    else:
        builder.button(text="🔔 Присылать каждое утро", callback_data="daily:on")
    builder.button(text="🔮 Сделать расклад", callback_data="menu:auto")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def tiers_menu(options: Sequence[Tier], currency: str = "₽") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tier in options:
        builder.button(text=tier.button_label(currency), callback_data=f"tier:{tier.key}")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def topics_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in TOPICS.items():
        builder.button(text=label, callback_data=f"topic:{key}")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def contact_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Не оставлять контакт", callback_data="order:nocontact")
    builder.adjust(1)
    return builder.as_markup()


def confirm_order_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить", callback_data="order:submit")
    builder.button(text="✏️ Переписать вопрос", callback_data="menu:author")
    builder.button(text="‹ Отменить", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def cancel_order_menu(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отменить заявку", callback_data=f"order:cancel:{order_id}")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Меню", callback_data="menu:main")]]
    )


def admin_order_menu(order_id: int, *, taken: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not taken:
        builder.button(text="✅ Взять в работу", callback_data=f"adm:take:{order_id}")
    builder.button(text="✍️ Ответить", callback_data=f"adm:answer:{order_id}")
    builder.button(text="🚫 Отклонить", callback_data=f"adm:decline:{order_id}")
    builder.adjust(1)
    return builder.as_markup()


def followup_menu(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Задать уточняющий вопрос", callback_data=f"order:followup:{order_id}")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def paywall_menu(price: int, currency: str) -> InlineKeyboardMarkup:
    """Оплата расклада: одна кнопка с ценой, вторая — назад."""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Оплатить {price} {currency}", callback_data="pay:auto")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def subscribe_menu() -> InlineKeyboardMarkup:
    """Подписка на карту дня — условие бесплатных раскладов."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔔 Подписаться на карту дня", callback_data="daily:on")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def profile_menu() -> InlineKeyboardMarkup:
    """Личный кабинет: что человек рассказал о себе и что может дополнить."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Дата рождения", callback_data="profile:birth_date")
    builder.button(text="Время рождения", callback_data="profile:birth_time")
    builder.button(text="Город рождения", callback_data="profile:birth_city")
    builder.button(text="Где живу сейчас", callback_data="profile:city")
    builder.button(text="‹ Меню", callback_data="menu:main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def number_menu(spread_key: str) -> InlineKeyboardMarkup:
    """Шаг с числом: человек называет своё или доверяет колоде."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Пусть выберет колода", callback_data=f"number:{spread_key}")
    builder.button(text="‹ К раскладкам", callback_data="menu:auto")
    builder.adjust(1)
    return builder.as_markup()
