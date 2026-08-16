"""Состояния диалогов."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AutoFlow(StatesGroup):
    waiting_question = State()


class AuthorFlow(StatesGroup):
    waiting_question = State()
    waiting_contact = State()
    confirming = State()
    waiting_followup = State()


class AdminFlow(StatesGroup):
    answering = State()
