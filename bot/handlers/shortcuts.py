"""Бот как дверь в приложение.

Карта дня, расклады, кабинет и разбор живут в мини-приложении: там экраны,
а не цепочка кнопок в переписке. Боту остаётся впустить — и команды, которые
люди уже выучили, не должны ломаться: каждая открывает нужный экран.

Роутер подключается первым, поэтому перехватывает команды раньше старых
сценариев. Сами сценарии пока на месте: приложение только вышло, и путь
назад должен существовать, пока не станет ясно, что он не нужен.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import analytics
from ..config import Settings
from ..db import Repo
from ..keyboards import app_menu
from .invite import greet_guest

router = Router(name="shortcuts")

# Команда → экран приложения. Экран передаётся якорем: приложение читает его
# при запуске и открывает нужную вкладку.
SCREENS: dict[str, tuple[str, str]] = {
    "daily": ("daily", "Карта дня — в приложении"),
    "deck": ("deck", "Колода целиком — в приложении"),
    "profile": ("profile", "Кабинет — в приложении"),
    "chart": ("profile", "Натальная карта — в кабинете приложения"),
    "forecast": ("profile", "Прогноз — в кабинете приложения"),
    "natal": ("profile", "Подробный разбор карты — в кабинете приложения"),
}

OPENING = "Открывается в приложении — одно нажатие."


def app_url(settings: Settings, screen: str = "") -> str | None:
    """Адрес приложения с нужным экраном или ничего, если адрес не настроен."""
    base = (settings.miniapp_url or "").strip()
    if not base.startswith("https://"):
        return None
    return f"{base}#{screen}" if screen else base


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    settings: Settings,
    repo: Repo,
) -> None:
    await state.clear()
    if message.from_user is not None:
        await repo.log_event(message.from_user.id, analytics.START)

    # Приглашение приходит той же командой: хвост ссылки надо прочитать до
    # того, как человек уйдёт в приложение, иначе пригласившего никто не вспомнит
    await greet_guest(message, settings, repo, command.args)

    url = app_url(settings)
    if url is None:
        # Адреса нет — пусть работают старые кнопки, чем ничего
        from .common import show_main_menu

        await show_main_menu(message, settings)
        return

    await message.answer(
        f"Здравствуйте. Расклады, карта дня и разборы {settings.author_name} — "
        "в приложении: карты видно целиком, а разбор не теряется в переписке.",
        reply_markup=app_menu(url),
    )


@router.message(Command(*SCREENS.keys()))
async def cmd_screen(message: Message, state: FSMContext, settings: Settings) -> None:
    """Старые команды остаются рабочими: каждая открывает свой экран."""
    await state.clear()
    name = (message.text or "").lstrip("/").split("@")[0].split()[0]
    screen, title = SCREENS.get(name, ("", OPENING))

    url = app_url(settings, screen)
    if url is None:
        # Адреса нет — отдаём команду старому сценарию, а не молчим
        raise SkipHandler

    await message.answer(f"{title}.\n{OPENING}", reply_markup=app_menu(url, "Открыть"))
