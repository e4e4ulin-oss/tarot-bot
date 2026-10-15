"""Личный кабинет: что человек рассказывает о себе.

Всё здесь необязательно. Пустой кабинет — рабочее состояние, а не недочёт:
расклад выходит и без него, просто разбор получается общий.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import profile as parse
from .. import texts
from ..db import Repo
from ..geo import by_id, find_all
from ..keyboards import places_menu, profile_menu
from ..states import ProfileFlow

router = Router(name="profile")

# Поле → как его разобрать, что переспросить, в каком состоянии ждать ответ
FIELDS = {
    "birth_date": (parse.parse_date, texts.PROFILE_BAD_DATE, ProfileFlow.birth_date),
    "birth_time": (parse.parse_time, texts.PROFILE_BAD_TIME, ProfileFlow.birth_time),
    "birth_city": (parse.parse_city, texts.PROFILE_BAD_CITY, ProfileFlow.birth_city),
    "city": (parse.parse_city, texts.PROFILE_BAD_CITY, ProfileFlow.city),
}


async def remember_place(repo: Repo, user_id: int, place) -> None:
    await repo.save_profile(
        user_id,
        birth_lat=place.latitude,
        birth_lon=place.longitude,
        birth_tz=place.timezone,
    )


async def show(message: Message, repo: Repo, *, edit: bool = False) -> None:
    user = await repo.get_user(message.chat.id)
    lines = parse.describe(user)
    text = texts.PROFILE_FILLED.format(lines=lines) if lines else texts.PROFILE_EMPTY
    if edit:
        await message.edit_text(text, reply_markup=profile_menu())
    else:
        await message.answer(text, reply_markup=profile_menu())


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext, repo: Repo) -> None:
    await state.clear()
    await show(message, repo)


@router.callback_query(F.data == "menu:profile")
async def cb_profile(call: CallbackQuery, state: FSMContext, repo: Repo) -> None:
    await state.clear()
    if isinstance(call.message, Message):
        await show(call.message, repo, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("profile:"))
async def cb_field(call: CallbackQuery, state: FSMContext) -> None:
    field = call.data.split(":", 1)[1]
    if field not in FIELDS:
        await call.answer()
        return

    await state.set_state(FIELDS[field][2])
    if isinstance(call.message, Message):
        await call.message.answer(texts.PROFILE_ASK[field])
    await call.answer()


@router.message(ProfileFlow.birth_date)
@router.message(ProfileFlow.birth_time)
@router.message(ProfileFlow.birth_city)
@router.message(ProfileFlow.city)
async def on_answer(message: Message, state: FSMContext, repo: Repo) -> None:
    current = await state.get_state()
    field = next(name for name, (_, _, flow) in FIELDS.items() if flow.state == current)
    parser, complaint, _ = FIELDS[field]

    if not message.text:
        await message.answer(complaint)
        return

    if parse.skipped(message.text):
        await state.clear()
        await message.answer(texts.PROFILE_SKIPPED)
        await show(message, repo)
        return

    value = parser(message.text)
    if value is None:
        await message.answer(complaint)
        return

    await repo.save_profile(message.chat.id, **{field: value})
    await state.clear()

    if field != "birth_city":
        await message.answer(texts.PROFILE_SAVED)
        await show(message, repo)
        return

    # Город рождения отвечает за асцендент, поэтому его судьбу человек должен
    # видеть сразу: нашёлся, не нашёлся или нашлось несколько тёзок
    places = find_all(str(value))
    if not places:
        await repo.save_profile(message.chat.id, birth_lat=None, birth_lon=None, birth_tz=None)
        await message.answer(texts.CITY_UNKNOWN)
    elif len(places) == 1:
        await remember_place(repo, message.chat.id, places[0])
        await message.answer(texts.CITY_FOUND.format(city=places[0].name))
    else:
        await message.answer(texts.CITY_MANY, reply_markup=places_menu(places[:6]))
        return

    await show(message, repo)


@router.callback_query(F.data.startswith("place:"))
async def cb_place(call: CallbackQuery, repo: Repo) -> None:
    place = by_id(int(call.data.split(":", 1)[1]))
    if place is None:
        await call.answer()
        return

    await remember_place(repo, call.from_user.id, place)
    if isinstance(call.message, Message):
        await call.message.answer(
            texts.CITY_CHOSEN.format(city=place.name, hint=place.hint)
        )
        await show(call.message, repo)
    await call.answer()
