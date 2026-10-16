"""Кнопка, открывающая колоду как мини-приложение Telegram.

Команда появляется только когда в окружении задан MINIAPP_URL: без адреса
кнопку показывать нечем, а обещать её в меню нечестно. Telegram открывает по
ней обычную веб-страницу внутри клиента, поэтому адрес обязан быть https.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from ..config import Settings

router = Router(name="miniapp")

TEXT = "Колода целиком: 78 карт на столе. Нажмите на карту, чтобы рассмотреть и перевернуть."
NO_URL = "Колода пока не опубликована."


def stamped(url: str, moment: datetime | None = None) -> str:
    """Адрес колоды с меткой часа.

    Telegram держит открытую страницу в своём кэше и после выкладки показывает
    прежнюю: нарисованная карта до человека не доезжает, а почистить кэш за
    него нельзя. Метка меняет адрес, и клиент идёт за свежим. Час — чтобы
    выкладка доходила быстро, но каждое открытие не качало колоду заново.
    """
    moment = moment or datetime.now(UTC)
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "v"]
    query.append(("v", moment.strftime("%Y%m%d%H")))
    return urlunsplit(parts._replace(query=urlencode(query)))


def deck_button(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть колоду", web_app=WebAppInfo(url=url))]]
    )


@router.message(Command("deck"))
async def open_deck(message: Message, settings: Settings) -> None:
    if not settings.miniapp_url:
        await message.answer(NO_URL)
        return
    await message.answer(TEXT, reply_markup=deck_button(stamped(settings.miniapp_url)))
