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
        # Ярлыки первыми: команда открывает приложение, а не старый сценарий
        "shortcuts",
        "common",
        "admin",
        "daily",
        "auto",
        "author",
        "invite",
        "miniapp",
        "payments",
        "profile",
        "chart",
        "natal",
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


def undefined_names(module) -> set[str]:
    """Имена, которые код читает как глобальные, но которых в модуле нет.

    Ровно та ошибка, на которой бот ушёл в перезапуск: вызов добавили,
    импорт — нет. Такое не ловится ни одним сценарным тестом, если функция
    в тестах не выполняется: main() как раз из них.
    """
    import builtins
    import dis

    seen: set[str] = set()

    def walk(code) -> None:
        for instruction in dis.get_instructions(code):
            if instruction.opname == "LOAD_GLOBAL":
                seen.add(instruction.argval)
        for const in code.co_consts:
            if hasattr(const, "co_code"):
                walk(const)

    # Сравниваем по файлу, а не по имени модуля: dataclass, Enum и SQLAlchemy
    # дописывают в наши классы методы, собранные в их собственных исходниках
    def ours(code) -> bool:
        return code is not None and code.co_filename == module.__file__

    for value in vars(module).values():
        code = getattr(value, "__code__", None)
        if ours(code):
            walk(code)
        elif isinstance(value, type):
            for member in vars(value).values():
                member_code = getattr(member, "__code__", None)
                if ours(member_code):
                    walk(member_code)

    known = set(vars(module)) | set(dir(builtins))
    return {name for name in seen if name not in known}


def test_every_module_has_what_it_calls():
    import importlib
    import pkgutil

    import bot

    broken: dict[str, set[str]] = {}
    for info in pkgutil.walk_packages(bot.__path__, prefix="bot."):
        module = importlib.import_module(info.name)
        missing = undefined_names(module)
        if missing:
            broken[info.name] = missing

    assert not broken, f"вызывают то, чего не импортировали: {broken}"


def test_the_refusal_keeps_the_provider_explanation():
    """400 без объяснения — это гадание; объяснение приходит в теле ответа."""
    import httpx

    from bot.services.grok import _explain

    body = {"error": {"message": "The model `grok-4` does not exist"}}
    assert "does not exist" in _explain(httpx.Response(400, json=body))

    plain = httpx.Response(400, text="Insufficient credits")
    assert "Insufficient credits" in _explain(plain)

    assert _explain(httpx.Response(400, text="")) == "без объяснения"


@pytest.mark.asyncio
async def test_a_refused_request_is_not_retried():
    """Отказ провайдера детерминирован: три одинаковых запроса — три отказа."""
    import httpx

    from bot.services.grok import GrokClient, Refused

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": {"message": "model not found"}})

    client = GrokClient("key", base_url="https://example.test/v1", retries=2)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/v1"
    )

    with pytest.raises(Refused) as refusal:
        await client.complete("система", "вопрос")

    assert attempts == 1
    assert "model not found" in refusal.value.explanation
    await client.close()


def test_the_deck_opens_with_an_hourly_stamp():
    """Выкладка должна доезжать до человека, а чужой кэш не чистится руками."""
    from datetime import UTC, datetime

    from bot.handlers.miniapp import stamped

    moment = datetime(2026, 10, 16, 3, 40, tzinfo=UTC)
    assert stamped("https://example.github.io/deck/", moment) == (
        "https://example.github.io/deck/?v=2026101603"
    )

    # Чужие параметры целы, своя метка не множится
    once = stamped("https://example.github.io/deck/?mode=dark", moment)
    assert once == "https://example.github.io/deck/?mode=dark&v=2026101603"
    assert stamped(once, moment) == once


def test_the_deck_passes_the_stamp_on_to_its_files():
    """Метка в адресе бессмысленна, если карточки грузятся прежними."""
    from pathlib import Path

    # Колода стала вкладкой приложения, но метка версии нужна там же, где
    # грузятся 78 картинок, — в её модуле
    module = Path(__file__).resolve().parents[1] / "miniapp" / "deck.js"
    text = module.read_text(encoding="utf-8")

    assert "const V = STAMP" in text
    assert "cards/${card.id}.jpg${V}" in text
    assert "cards.json${V}" in text


def test_the_web_script_and_the_bot_agree_on_the_port():
    """Разойдутся — приложение откроется, а данные к нему не придут.

    На работающем сервере это уже совпало руками; ломается оно у следующего,
    кто поставит бота с нуля, и выглядит как «всё белое и ничего не грузится».
    """
    import re
    from pathlib import Path

    from bot.config import Settings

    script = Path(__file__).resolve().parents[1] / "deploy" / "web.sh"
    fallback = re.search(r'PORT="\$\{PORT:-(\d+)\}"', script.read_text(encoding="utf-8"))

    assert fallback, "в скрипте не нашёлся порт по умолчанию"
    assert int(fallback.group(1)) == Settings().web_port


def test_the_deck_is_a_tab_not_a_page():
    """Отдельная страница ломалась в Telegram, и чинить там было нечего.

    Подпись приходит в адресе при открытии; уход на другой документ её
    затирает, и приложение грузится без неё — вкладки видно, а за ними пусто.
    Поэтому колода живёт внутри приложения, а старый адрес только
    перенаправляет.
    """
    from pathlib import Path

    miniapp = Path(__file__).resolve().parents[1] / "miniapp"
    index = (miniapp / "index.html").read_text(encoding="utf-8")
    old_page = (miniapp / "deck.html").read_text(encoding="utf-8")

    assert 'id="s-deck"' in index, "в приложении нет экрана колоды"
    assert 'src="deck.js"' in index, "модуль колоды не подключён"
    assert 'data-go="deck"' in index, "в панели нет вкладки колоды"

    # Старый адрес не рисует колоду заново, а отдаёт её приложению
    assert "location.replace" in old_page
    assert "#deck" in old_page


def test_the_app_asks_for_the_whole_screen():
    """Во весь экран — и с запасом под кнопки Telegram поверх страницы.

    Полноэкранный режим появился в Bot API 8.0, поэтому просить его вслепую
    нельзя: на старом клиенте вызова просто нет. А когда он включён, сверху
    висят «закрыть» и «меню» — без отступа под них шапка оказывается под
    кнопками, и по ней невозможно попасть.
    """
    from pathlib import Path

    miniapp = Path(__file__).resolve().parents[1] / "miniapp"
    script = (miniapp / "app.js").read_text(encoding="utf-8")
    styles = (miniapp / "app.css").read_text(encoding="utf-8")

    assert "requestFullscreen" in script
    assert 'isVersionAtLeast("8.0")' in script, "просим режим, не проверив версию"
    assert "fullscreenChanged" in script, "состояние режима меняется и после запуска"

    assert "html.fullscreen" in styles
    assert "--tg-content-safe-area-inset-top" in styles
    assert "--tg-safe-area-inset-bottom" in styles


def test_the_app_can_read_the_author_answer():
    """Самый дорогой товар не должен жить наполовину в переписке.

    Ответ хранится, значит приложению есть что показать; эти три нити —
    список заявок, сама заявка и уточнение — и делают разбор читаемым там,
    где за него заплатили.
    """
    from pathlib import Path

    app = (Path(__file__).resolve().parents[1] / "miniapp" / "app.js").read_text("utf-8")

    assert 'api("orders")' in app
    assert "authorThread" in app
    assert "/followup" in app


def test_the_answer_is_written_down_not_only_forwarded():
    """Копия в чате исчезает из виду; запись остаётся."""
    from pathlib import Path

    admin = (Path(__file__).resolve().parents[1] / "bot" / "handlers" / "admin.py").read_text(
        "utf-8"
    )

    assert 'add_order_message(order.id, "author"' in admin
    # Записываем до смены статуса: отвеченная заявка без ответа — ложь
    assert admin.index("add_order_message") < admin.index("OrderStatus.ANSWERED, admin_id")
