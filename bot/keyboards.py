"""Инлайн-клавиатуры."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .tarot import SPREAD_ORDER, SPREADS
from .texts import TOPICS


def main_menu(author_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔮 Авто-расклад", callback_data="menu:auto")
    builder.button(text=f"✍️ Авторский разбор от {author_name}", callback_data="menu:author")
    builder.button(text="📜 Мои расклады", callback_data="menu:history")
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
    builder.button(text=f"✍️ Разбор от {author_name}", callback_data="menu:author")
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
