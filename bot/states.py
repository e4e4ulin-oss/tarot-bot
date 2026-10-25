"""Состояния диалогов."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AutoFlow(StatesGroup):
    waiting_question = State()
    # Число называет человек: расклад должен быть его, а не бота
    waiting_number = State()


class ProfileFlow(StatesGroup):
    birth_date = State()
    birth_time = State()
    birth_city = State()
    city = State()


class PromoFlow(StatesGroup):
    waiting_code = State()


class AuthorFlow(StatesGroup):
    waiting_question = State()
    waiting_contact = State()
    confirming = State()
    waiting_followup = State()


class AdminFlow(StatesGroup):
    answering = State()
