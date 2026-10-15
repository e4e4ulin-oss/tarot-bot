"""Проверяем, что бот собирается: роутеры, клавиатуры, фильтр доступа."""

import pytest
from aiogram.types import Chat, Message, User

from bot.config import Settings
from bot.filters import IsAdmin
from bot.handlers import ROUTERS
from bot.keyboards import admin_order_menu, main_menu, spreads_menu, topics_menu
from bot.tarot import SPREAD_ORDER


def _message(chat_id: int, user_id: int) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Кто-то"),
    ).as_(None)


def test_router_order_keeps_fallback_last():
    assert [router.name for router in ROUTERS] == [
        "common",
        "admin",
        "daily",
        "auto",
        "author",
        "miniapp",
        "payments",
        "fallback",
    ]


def test_main_menu_has_both_modes():
    markup = main_menu("таролог")
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "menu:auto" in data
    assert "menu:author" in data


def test_spreads_menu_lists_every_spread():
    markup = spreads_menu()
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    for key in SPREAD_ORDER:
        assert f"spread:{key}" in data


def test_topics_menu_and_admin_menu_callbacks():
    topics = [b.callback_data for row in topics_menu().inline_keyboard for b in row]
    assert "topic:love" in topics

    fresh = [b.callback_data for row in admin_order_menu(7).inline_keyboard for b in row]
    assert fresh == ["adm:take:7", "adm:answer:7", "adm:decline:7"]

    taken = [
        b.callback_data for row in admin_order_menu(7, taken=True).inline_keyboard for b in row
    ]
    assert "adm:take:7" not in taken


@pytest.mark.asyncio
async def test_is_admin_filter():
    settings = Settings(BOT_TOKEN="t", ADMIN_IDS="10", ADMIN_CHAT_ID=-100500)
    check = IsAdmin()

    assert await check(_message(chat_id=10, user_id=10), settings) is True
    assert await check(_message(chat_id=-100500, user_id=777), settings) is True
    assert await check(_message(chat_id=777, user_id=777), settings) is False


def test_author_name_fits_any_value():
    """Имя автора подставляется в именительном падеже — подойдёт любое значение."""
    from bot import texts

    for name in ("таролог", "Мария", "Анна Петрова"):
        for template in (
            texts.START,
            texts.AUTHOR_INTRO,
            texts.AUTHOR_SENT,
            texts.AUTHOR_ANSWER_HEADER,
            texts.AUTHOR_FOLLOWUP_ASK,
            texts.AUTHOR_FOLLOWUP_SENT,
            texts.AUTHOR_DECLINED,
        ):
            rendered = template.format(
                author=name, order_id=1, deadline="2 часов", work_hours="", left="1 вопрос"
            )
            assert f"от {name}" not in rendered, (name, rendered[:60])
            assert f"у {name}" not in rendered, (name, rendered[:60])


def test_deck_button_needs_an_address():
    from bot.handlers.miniapp import deck_button

    markup = deck_button("https://taro.example.com")
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == "https://taro.example.com"


def test_deck_is_published_without_extra_setup():
    """Адрес колоды — свойство репозитория, а не настройка сервера."""
    settings = Settings(BOT_TOKEN="x")
    assert settings.miniapp_url.startswith("https://")
