"""Меню, справка, история и отмена."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..config import Settings
from ..db import Repo
from ..keyboards import back_to_menu, main_menu
from ..tarot import SPREADS, deserialize
from ..utils import fmt_date

router = Router(name="common")
# Подключается последним: ловит всё, что не подошло ни одному сценарию.
fallback_router = Router(name="fallback")


async def show_main_menu(message: Message, settings: Settings, *, edit: bool = False) -> None:
    text = texts.START.format(author=settings.author_name)
    markup = main_menu(settings.author_name)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await show_main_menu(message, settings)


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.HELP.format(author=settings.author_name), reply_markup=back_to_menu()
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL)
        return
    await state.clear()
    await message.answer(texts.CANCELLED)
    await show_main_menu(message, settings)


@router.callback_query(F.data == "menu:main")
async def cb_main(call: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    if isinstance(call.message, Message):
        await show_main_menu(call.message, settings, edit=True)
    await call.answer()


@router.callback_query(F.data == "menu:about")
async def cb_about(call: CallbackQuery, settings: Settings) -> None:
    if isinstance(call.message, Message):
        await call.message.edit_text(
            texts.ABOUT.format(author=settings.author_name), reply_markup=back_to_menu()
        )
    await call.answer()


@router.callback_query(F.data == "menu:history")
async def cb_history(call: CallbackQuery, repo: Repo) -> None:
    readings = await repo.last_readings(call.from_user.id, limit=5)
    if not readings:
        await call.answer()
        if isinstance(call.message, Message):
            await call.message.edit_text(texts.NO_READINGS, reply_markup=back_to_menu())
        return

    blocks = [texts.HISTORY_HEADER, ""]
    for reading in readings:
        spread = SPREADS.get(reading.spread_key)
        title = spread.title if spread else reading.spread_key
        drawn = deserialize(reading.cards)
        cards = ", ".join(item.title for item in drawn)
        blocks.append(f"<b>{fmt_date(reading.created_at)}</b> · {texts.esc(title)}")
        if reading.question:
            blocks.append(f"<i>{texts.esc(texts.short(reading.question, 90))}</i>")
        blocks.append(texts.esc(cards))
        blocks.append("")

    if isinstance(call.message, Message):
        await call.message.edit_text("\n".join(blocks).strip(), reply_markup=back_to_menu())
    await call.answer()


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    """Помогает при настройке: показывает id чата и пользователя."""
    await message.answer(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"user_id: <code>{message.from_user.id if message.from_user else '—'}</code>"
    )


@fallback_router.message(F.chat.type == "private")
async def fallback(message: Message, settings: Settings) -> None:
    """Любое сообщение вне сценария возвращает человека в меню."""
    await message.answer(
        "Не понял сообщение. Выберите действие в меню:",
        reply_markup=main_menu(settings.author_name),
    )
