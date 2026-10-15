"""Кнопка, открывающая колоду как мини-приложение Telegram.

Команда появляется только когда в окружении задан MINIAPP_URL: без адреса
кнопку показывать нечем, а обещать её в меню нечестно. Telegram открывает по
ней обычную веб-страницу внутри клиента, поэтому адрес обязан быть https.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from ..config import Settings

router = Router(name="miniapp")

TEXT = "Колода целиком: 78 карт на столе. Нажмите на карту, чтобы рассмотреть и перевернуть."
NO_URL = "Колода пока не опубликована."


def deck_button(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть колоду", web_app=WebAppInfo(url=url))]]
    )


@router.message(Command("deck"))
async def open_deck(message: Message, settings: Settings) -> None:
    if not settings.miniapp_url:
        await message.answer(NO_URL)
        return
    await message.answer(TEXT, reply_markup=deck_button(settings.miniapp_url))
