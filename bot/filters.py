"""Фильтры доступа."""

from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .config import Settings


class IsAdmin(Filter):
    """Автор бота: пользователь из ADMIN_IDS либо любое сообщение из админ-чата."""

    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        user: User | None = None
        chat_id: int | None = None

        if isinstance(event, Message):
            user = event.from_user
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            chat_id = event.message.chat.id if event.message else None

        if user is not None and settings.is_admin(user.id):
            return True
        return bool(settings.admin_chat_id) and chat_id == settings.admin_chat_id
