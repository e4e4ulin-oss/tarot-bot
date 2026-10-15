"""Интеграционный прогон: настоящие апдейты через диспетчер с подменённой сессией Telegram.

Роутеры aiogram объявлены на уровне модулей, поэтому диспетчер собирается один раз на модуль,
а изоляция тестов достигается разными id чатов.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Chat, MessageId, PhotoSize, Update, User
from aiogram.types import Message as TgMessage

from bot.__main__ import build_dispatcher
from bot.tenants import ensure_default
from bot.config import Settings
from bot.db import OrderStatus, Repo, create_engine, create_session_factory, init_models
from bot.services import UPDATE_APPLIED, CommandResult, GrokClient, Interpreter
from bot.tarot import get_card
from bot.tarot.images import cache_key

ADMIN_CHAT_ID = -100999
ADMIN_ID = 777


class FakeSession(BaseSession):
    """Ловит вызовы Bot API и возвращает правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._message_id = 1000

    async def close(self) -> None:  # pragma: no cover - интерфейс BaseSession
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - не используется
        yield b""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod,
        timeout: int | None = None,  # noqa: ASYNC109 - сигнатура задана BaseSession
    ):
        self.calls.append(method)
        name = type(method).__name__
        self._message_id += 1

        if name in {"SendMessage", "EditMessageText", "SendPhoto"}:
            return TgMessage(
                message_id=self._message_id,
                date=datetime.now(UTC),
                chat=Chat(id=getattr(method, "chat_id", 0), type="private"),
                text=getattr(method, "text", None),
            ).as_(bot)
        if name == "CopyMessage":
            return MessageId(message_id=self._message_id)
        if name == "SendMediaGroup":
            return [
                TgMessage(
                    message_id=self._message_id + offset,
                    date=datetime.now(UTC),
                    chat=Chat(id=method.chat_id, type="private"),
                    photo=[
                        PhotoSize(
                            file_id=f"file-{self._message_id + offset}",
                            file_unique_id=f"uniq-{self._message_id + offset}",
                            width=1112,
                            height=1920,
                        )
                    ],
                ).as_(bot)
                for offset in range(len(method.media))
            ]
        return True

    def methods(self, name: str) -> list[TelegramMethod]:
        return [call for call in self.calls if type(call).__name__ == name]

    def texts(self, name: str = "SendMessage") -> list[str]:
        return [getattr(call, "text", "") or "" for call in self.methods(name)]

    def clear(self) -> None:
        self.calls.clear()


def make_message(text: str, *, chat_id: int, user_id: int | None = None) -> TgMessage:
    return TgMessage(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id or chat_id, is_bot=False, first_name="Клиент"),
        text=text,
    )


def make_callback(data: str, *, chat_id: int, user_id: int | None = None) -> CallbackQuery:
    return CallbackQuery(
        id="cb-1",
        from_user=User(id=user_id or chat_id, is_bot=False, first_name="Клиент"),
        chat_instance="ci",
        data=data,
        message=TgMessage(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
            text="…",
        ),
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "flow.db"
    settings = Settings(
        BOT_TOKEN="123:abc",
        ADMIN_CHAT_ID=ADMIN_CHAT_ID,
        ADMIN_IDS=str(ADMIN_ID),
        DATABASE_URL=f"sqlite+aiosqlite:///{db_path}",
        XAI_API_KEY="",
        # Эти тесты про сценарий расклада, а не про доступ к нему:
        # ворота открыты, лимит проверяется отдельным тестом ниже.
        FREE_AUTO_READINGS=50,
        FREE_NEEDS_DAILY=False,
        DAILY_AUTO_LIMIT=5,
    )
    engine = create_engine(settings.database_url)
    await init_models(engine)
    factory = create_session_factory(engine)

    # Боту нужен арендатор: без него мидлварь не знает, чьи настройки брать,
    # и молчит — ровно то, что должно происходить с чужим ботом
    async with factory() as db:
        await ensure_default(db, settings)

    session = FakeSession()
    bot = Bot(
        settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(settings, Interpreter(GrokClient("")), factory, throttle_rate=0)

    yield dp, bot, session, factory

    await bot.session.close()
    await engine.dispose()


@pytest.fixture
def env(app):
    dp, bot, session, factory = app
    session.clear()
    return dp, bot, session, factory


async def forget_cached_image(db, key: str) -> None:
    """Убирает запись кэша: база общая на модуль, и соседний тест мог её создать."""
    from sqlalchemy import delete

    from bot.db import CardImage

    await db.execute(delete(CardImage).where(CardImage.card_id == key))
    await db.commit()


async def feed(dp, bot, event, update_id: int = 1) -> None:
    if isinstance(event, CallbackQuery):
        await dp.feed_update(bot, Update(update_id=update_id, callback_query=event))
    else:
        await dp.feed_update(bot, Update(update_id=update_id, message=event))


@pytest.mark.asyncio(loop_scope="module")
async def test_start_shows_menu(env):
    dp, bot, session, _ = env
    await feed(dp, bot, make_message("/start", chat_id=1001))
    assert any("Авторский разбор" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_auto_reading_end_to_end(env):
    dp, bot, session, factory = env
    chat = 1002

    await feed(dp, bot, make_message("/start", chat_id=chat), 1)
    await feed(dp, bot, make_callback("menu:auto", chat_id=chat), 2)
    await feed(dp, bot, make_callback("spread:ptf", chat_id=chat), 3)
    session.clear()
    await feed(dp, bot, make_message("Что мне мешает переехать?", chat_id=chat), 4)

    sent = session.texts()
    assert any("Прошлое" in text for text in sent), sent
    assert any("Что мне мешает переехать?" in text for text in sent)

    async with factory() as db:
        readings = await Repo(db).last_readings(chat)
    assert len(readings) == 1
    assert readings[0].spread_key == "ptf"
    assert readings[0].ai_used is False  # ключа Grok нет — сработал резервный разбор
    assert len(readings[0].cards) == 3


@pytest.mark.asyncio(loop_scope="module")
async def test_reading_without_question(env):
    dp, bot, session, factory = env
    chat = 1003

    await feed(dp, bot, make_callback("draw:day", chat_id=chat), 1)

    async with factory() as db:
        readings = await Repo(db).last_readings(chat)
    assert len(readings) == 1
    assert readings[0].question is None
    assert len(readings[0].cards) == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_daily_limit_blocks_extra_readings(env):
    dp, bot, session, factory = env
    chat = 1004

    async with factory() as db:
        repo = Repo(db)
        await repo.upsert_user(chat, None, "Клиент")
        for _ in range(5):
            await repo.save_reading(
                user_id=chat,
                spread_key="day",
                question=None,
                cards=[],
                interpretation="",
                ai_used=False,
            )

    session.clear()
    await feed(dp, bot, make_callback("draw:day", chat_id=chat), 1)
    assert any("лимит" in text for text in session.texts())

    async with factory() as db:
        assert await Repo(db).count_readings_today(chat) == 5


@pytest.mark.asyncio(loop_scope="module")
async def test_author_order_flow_reaches_admin_and_back(env):
    dp, bot, session, factory = env
    chat = 1005

    await feed(dp, bot, make_message("/start", chat_id=chat), 1)
    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 2)
    await feed(dp, bot, make_callback("topic:love", chat_id=chat), 3)
    await feed(dp, bot, make_message("Расскажите про мои отношения", chat_id=chat), 4)
    await feed(dp, bot, make_callback("order:nocontact", chat_id=chat), 5)
    session.clear()
    await feed(dp, bot, make_callback("order:submit", chat_id=chat), 6)

    admin_messages = [
        call for call in session.methods("SendMessage") if call.chat_id == ADMIN_CHAT_ID
    ]
    assert admin_messages, session.calls
    assert "Расскажите про мои отношения" in admin_messages[0].text

    async with factory() as db:
        order = await Repo(db).open_order_for_user(chat)
    assert order is not None
    assert order.topic == "love"
    assert order.status is OrderStatus.NEW
    assert order.admin_message_id is not None

    # автор отвечает из админ-чата
    session.clear()
    await feed(
        dp,
        bot,
        make_callback(f"adm:answer:{order.id}", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID),
        7,
    )
    await feed(
        dp,
        bot,
        make_message("Вот ваш разбор.", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID),
        8,
    )

    copies = session.methods("CopyMessage")
    assert copies and copies[0].chat_id == chat

    async with factory() as db:
        refreshed = await Repo(db).get_order(order.id)
    assert refreshed.status is OrderStatus.ANSWERED
    assert refreshed.admin_id == ADMIN_ID


@pytest.mark.asyncio(loop_scope="module")
async def test_second_order_is_refused_while_one_is_open(env):
    dp, bot, session, factory = env
    chat = 1006

    async with factory() as db:
        repo = Repo(db)
        await repo.upsert_user(chat, None, "Клиент")
        order = await repo.create_order(
            user_id=chat, question="Первый вопрос", topic=None, contact=None
        )

    session.clear()
    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 1)
    edits = session.texts("EditMessageText")
    assert any(f"№{order.id}" in text for text in edits), edits


@pytest.mark.asyncio(loop_scope="module")
async def test_client_cannot_run_admin_actions(env):
    dp, bot, session, factory = env
    chat = 1007

    async with factory() as db:
        repo = Repo(db)
        await repo.upsert_user(chat, None, "Клиент")
        order = await repo.create_order(user_id=chat, question="Вопрос", topic=None, contact=None)

    session.clear()
    await feed(dp, bot, make_callback(f"adm:decline:{order.id}", chat_id=chat), 1)

    async with factory() as db:
        refreshed = await Repo(db).get_order(order.id)
    assert refreshed.status is OrderStatus.NEW
    assert not session.methods("SendMessage")


@pytest.mark.asyncio(loop_scope="module")
async def test_diag_reports_missing_grok_key(env):
    """Автор должен видеть причину прямо в боте, не залезая в логи хостинга."""
    dp, bot, session, _ = env
    session.clear()

    await feed(dp, bot, make_message("/diag", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    texts_sent = session.texts()
    assert texts_sent, session.calls
    report = texts_sent[0]
    assert "AI_API_KEY" in report
    assert "Диагностика" in report


@pytest.mark.asyncio(loop_scope="module")
async def test_diag_is_admin_only(env):
    """Обычный клиент команду не получает — она уходит в общий фолбэк."""
    dp, bot, session, _ = env
    session.clear()

    await feed(dp, bot, make_message("/diag", chat_id=2001), 1)

    sent = session.texts()
    assert all("Диагностика" not in text for text in sent)
    assert any("только автору" in text for text in sent), sent


@pytest.mark.asyncio(loop_scope="module")
async def test_reading_sends_card_images_and_caches_file_ids(env):
    """Карты уходят картинками, а полученный file_id сохраняется для повторов."""
    dp, bot, session, factory = env
    chat = 1008

    await feed(dp, bot, make_callback("draw:day", chat_id=chat), 1)

    albums = session.methods("SendMediaGroup")
    assert albums, session.calls
    assert len(albums[0].media) == 1

    async with factory() as db:
        readings = await Repo(db).last_readings(chat)
        drawn_card = readings[0].cards[0]
        # У перевёрнутой карты своя, повёрнутая картинка — значит и свой ключ кэша
        key = cache_key(get_card(drawn_card["card"]), drawn_card["reversed"])
        cached = await Repo(db).card_file_ids([key])
    assert cached.get(key), "file_id карты не сохранился"

    # та же карта во второй раз уходит уже по file_id, без обращения к Викискладу
    from bot.handlers.auto import send_card_images
    from bot.tarot import deserialize

    drawn = deserialize(readings[0].cards)
    anchor = TgMessage(
        message_id=99,
        date=datetime.now(UTC),
        chat=Chat(id=chat, type="private"),
    ).as_(bot)

    session.clear()
    async with factory() as db:
        await send_card_images(bot, anchor.chat.id, drawn, Repo(db), dp["settings"])

    sources = [item.media for item in session.methods("SendMediaGroup")[0].media]
    assert sources == [cached[key]], sources


@pytest.mark.asyncio(loop_scope="module")
async def test_order_reaches_every_admin(env):
    """Заявка приходит и в админ-чат, и в личку каждого, кто ведёт разборы."""
    dp, bot, session, factory = env
    chat = 1009

    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 1)
    await feed(dp, bot, make_callback("topic:work", chat_id=chat), 2)
    await feed(dp, bot, make_message("Что с работой?", chat_id=chat), 3)
    await feed(dp, bot, make_callback("order:nocontact", chat_id=chat), 4)
    session.clear()
    await feed(dp, bot, make_callback("order:submit", chat_id=chat), 5)

    recipients = [
        call.chat_id for call in session.methods("SendMessage") if "Заявка" in (call.text or "")
    ]
    assert recipients == [ADMIN_CHAT_ID, ADMIN_ID], recipients


class StubServer:
    """Служба бота без службы: команды только записываются."""

    def __init__(self) -> None:
        self.logs_result = CommandResult(0, "строка журнала")
        self.update_result = CommandResult(0, "уже свежая версия (abc1234)")
        self.asked_lines: list[int] = []
        self.restarts = 0

    async def logs(self, lines: int) -> CommandResult:
        self.asked_lines.append(lines)
        return self.logs_result

    async def update(self) -> CommandResult:
        return self.update_result

    async def restart(self) -> CommandResult:
        self.restarts += 1
        return CommandResult(0, "")


@pytest.fixture
def server(env):
    dp, *_ = env
    original = dp["server"]
    stub = StubServer()
    dp["server"] = stub
    yield stub
    dp["server"] = original


@pytest.mark.asyncio(loop_scope="module")
async def test_logs_returns_the_journal_tail(env, server):
    dp, bot, session, _ = env

    await feed(dp, bot, make_message("/logs", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.asked_lines == [40]
    assert any("Журнал бота" in text and "строка журнала" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_logs_accepts_a_line_count(env, server):
    dp, bot, session, _ = env

    await feed(dp, bot, make_message("/logs 120", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.asked_lines == [120]


@pytest.mark.asyncio(loop_scope="module")
async def test_logs_explains_itself_instead_of_guessing(env, server):
    dp, bot, session, _ = env

    await feed(dp, bot, make_message("/logs вчера", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.asked_lines == []
    assert any("Сколько последних строк" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_update_on_fresh_code_restarts_nothing(env, server):
    dp, bot, session, _ = env

    await feed(dp, bot, make_message("/update", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.restarts == 0
    assert any("уже свежий" in text for text in session.texts("EditMessageText"))


@pytest.mark.asyncio(loop_scope="module")
async def test_update_applies_changes_then_restarts(env, server):
    dp, bot, session, _ = env
    server.update_result = CommandResult(UPDATE_APPLIED, "обновление abc1234 → def5678")

    await feed(dp, bot, make_message("/update", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    # Сначала ответ, потом перезапуск: иначе бота остановят до отправки сообщения.
    texts_sent = session.texts("EditMessageText")
    assert any("Правки загружены" in text for text in texts_sent)
    assert server.restarts == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_update_keeps_the_bot_running_when_it_fails(env, server):
    dp, bot, session, _ = env
    server.update_result = CommandResult(1, "код не компилируется — откатываюсь")

    await feed(dp, bot, make_message("/update", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.restarts == 0
    assert any("прежней версии" in text for text in session.texts("EditMessageText"))


@pytest.mark.asyncio(loop_scope="module")
async def test_unavailable_command_says_what_to_fix(env, server):
    dp, bot, session, _ = env
    server.update_result = CommandResult(127, "sudo: command not found")

    await feed(dp, bot, make_message("/update", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.restarts == 0
    assert any("установщика" in text for text in session.texts("EditMessageText"))


@pytest.mark.asyncio(loop_scope="module")
async def test_restart_warns_before_going_down(env, server):
    dp, bot, session, _ = env

    await feed(dp, bot, make_message("/restart", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert server.restarts == 1
    assert any("Перезапускаю" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_service_commands_are_closed_to_everyone_else(env, server):
    dp, bot, session, _ = env

    await feed(dp, bot, make_message("/update", chat_id=4242), 1)
    await feed(dp, bot, make_message("/restart", chat_id=4242), 2)
    await feed(dp, bot, make_message("/logs", chat_id=4242), 3)

    assert server.restarts == 0
    assert server.asked_lines == []
    assert all("только автору" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_author_flow_starts_with_prices(env):
    """Цену человек видит до того, как напишет вопрос, а не после."""
    dp, bot, session, _ = env

    await feed(dp, bot, make_callback("menu:author", chat_id=1101), 1)

    shown = session.texts("EditMessageText")[-1]
    assert "Лаконичный" in shown and "500 ₽" in shown
    assert "Базовый" in shown and "700 ₽" in shown
    assert "Полный" in shown and "1000 ₽" in shown


@pytest.mark.asyncio(loop_scope="module")
async def test_chosen_level_reaches_the_order_and_the_author(env):
    dp, bot, session, factory = env
    chat = 1102

    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 1)
    await feed(dp, bot, make_callback("tier:full", chat_id=chat), 2)
    await feed(dp, bot, make_callback("topic:love", chat_id=chat), 3)
    await feed(dp, bot, make_message("Что у нас дальше?", chat_id=chat), 4)
    await feed(dp, bot, make_callback("order:nocontact", chat_id=chat), 5)

    confirm = session.texts()[-1]
    assert "Полный · 1000 ₽" in confirm

    session.clear()
    await feed(dp, bot, make_callback("order:submit", chat_id=chat), 6)

    async with factory() as db:
        order = (await Repo(db).open_orders())[-1]
    assert order.tier == "full"

    for_author = [text for text in session.texts() if "Заявка" in text]
    assert for_author and "Полный · 1000 ₽" in for_author[0]


@pytest.mark.asyncio(loop_scope="module")
async def test_orders_list_shows_levels(env):
    """В /orders видно, за какие деньги каждая заявка — иначе их не разложить по приоритету."""
    dp, bot, session, _ = env

    session.clear()
    await feed(dp, bot, make_message("/orders", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    listing = session.texts()[0]
    assert "Открытые заявки" in listing
    assert "·" in listing


@pytest.mark.asyncio(loop_scope="module")
async def test_single_order_card_opens(env):
    dp, bot, session, factory = env
    async with factory() as db:
        order = (await Repo(db).open_orders())[-1]

    session.clear()
    await feed(
        dp, bot, make_message(f"/order {order.id}", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1
    )

    card = session.texts()[0]
    assert f"Заявка №{order.id}" in card
    assert "Уровень:" in card


async def place_order(dp, bot, chat: int, tier: str) -> int:
    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 1)
    await feed(dp, bot, make_callback(f"tier:{tier}", chat_id=chat), 2)
    await feed(dp, bot, make_callback("topic:work", chat_id=chat), 3)
    await feed(dp, bot, make_message("Вопрос по работе", chat_id=chat), 4)
    await feed(dp, bot, make_callback("order:nocontact", chat_id=chat), 5)
    await feed(dp, bot, make_callback("order:submit", chat_id=chat), 6)
    return chat


async def answer_order(dp, bot, factory, chat: int) -> int:
    """Автор берёт последнюю заявку этого чата и отвечает на неё."""
    async with factory() as db:
        order = (await Repo(db).open_order_for_user(chat))
    await feed(
        dp,
        bot,
        make_callback(f"adm:answer:{order.id}", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID),
        7,
    )
    await feed(
        dp, bot, make_message("Вот разбор.", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 8
    )
    return order.id


@pytest.mark.asyncio(loop_scope="module")
async def test_short_level_offers_no_followup_button(env):
    """Лаконичный уровень уточнений не обещает — кнопки быть не должно."""
    dp, bot, session, factory = env
    chat = 1201

    await place_order(dp, bot, chat, "short")
    session.clear()
    await answer_order(dp, bot, factory, chat)

    to_client = [call for call in session.methods("SendMessage") if call.chat_id == chat]
    buttons = [
        button.callback_data
        for call in to_client
        if call.reply_markup
        for row in call.reply_markup.inline_keyboard
        for button in row
    ]
    assert not any(data.startswith("order:followup") for data in buttons)
    assert any("оформите новую заявку" in (call.text or "") for call in to_client)


@pytest.mark.asyncio(loop_scope="module")
async def test_base_level_spends_its_single_followup(env):
    dp, bot, session, factory = env
    chat = 1202

    await place_order(dp, bot, chat, "base")
    order_id = await answer_order(dp, bot, factory, chat)

    session.clear()
    await feed(dp, bot, make_callback(f"order:followup:{order_id}", chat_id=chat), 9)
    await feed(dp, bot, make_message("А что с деньгами?", chat_id=chat), 10)

    assert any("Отправлено" in text for text in session.texts())
    async with factory() as db:
        assert (await Repo(db).get_order(order_id)).followups_used == 1

    # Второй раз кнопка из старого сообщения уже не должна работать.
    session.clear()
    await feed(dp, bot, make_callback(f"order:followup:{order_id}", chat_id=chat), 11)

    assert any("Уточнения по этой заявке закончились" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_full_level_allows_three_then_stops(env):
    dp, bot, session, factory = env
    chat = 1203

    await place_order(dp, bot, chat, "full")
    order_id = await answer_order(dp, bot, factory, chat)

    for number in range(3):
        await feed(dp, bot, make_callback(f"order:followup:{order_id}", chat_id=chat), 20 + number)
        await feed(dp, bot, make_message(f"Уточнение {number + 1}", chat_id=chat), 30 + number)

    async with factory() as db:
        assert (await Repo(db).get_order(order_id)).followups_used == 3

    session.clear()
    await feed(dp, bot, make_callback(f"order:followup:{order_id}", chat_id=chat), 40)
    assert any("закончились" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_client_is_told_about_prepayment(env):
    dp, bot, session, _ = env
    chat = 1204

    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 1)
    session.clear()
    await feed(dp, bot, make_callback("tier:base", chat_id=chat), 2)

    assert any("до начала работы" in text for text in session.texts("EditMessageText"))


@pytest.mark.asyncio(loop_scope="module")
async def test_order_outside_working_hours_says_when_the_clock_starts(env, monkeypatch):
    """Заявка в ночь субботы не должна выглядеть как обещание ответить ночью."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    saturday_night = datetime(2026, 10, 17, 2, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("bot.handlers.author.now", lambda settings: saturday_night)

    dp, bot, session, _ = env
    chat = 1301

    await place_order(dp, bot, chat, "base")

    sent = session.texts("EditMessageText")[-1]
    assert "в течение 2 часов после оплаты" in sent
    assert "Сейчас нерабочее время" in sent
    assert "в понедельник в 9:00" in sent


@pytest.mark.asyncio(loop_scope="module")
async def test_author_sees_the_deadline_when_taking_an_order(env, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    monday = datetime(2026, 10, 19, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("bot.handlers.admin.now", lambda settings: monday)

    dp, bot, session, factory = env
    chat = 1302
    await place_order(dp, bot, chat, "full")
    async with factory() as db:
        order = await Repo(db).open_order_for_user(chat)

    session.clear()
    await feed(
        dp, bot, make_callback(f"adm:take:{order.id}", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 7
    )

    assert any("Ответить до: <b>сегодня в 13:00</b> (мск)" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_funnel_follows_a_person_through_the_bot(env):
    """Шаги должны отмечаться сами по ходу сценария, а не вручную в тестах."""
    from bot import analytics

    dp, bot, session, factory = env
    chat = 1401

    await feed(dp, bot, make_message("/start", chat_id=chat), 1)
    await feed(dp, bot, make_callback("menu:author", chat_id=chat), 2)
    await feed(dp, bot, make_callback("tier:base", chat_id=chat), 3)
    await feed(dp, bot, make_callback("topic:work", chat_id=chat), 4)
    await feed(dp, bot, make_message("Вопрос", chat_id=chat), 5)
    await feed(dp, bot, make_callback("order:nocontact", chat_id=chat), 6)
    await feed(dp, bot, make_callback("order:submit", chat_id=chat), 7)

    async with factory() as db:
        steps = await Repo(db).funnel(tuple(name for name, _ in analytics.FUNNEL))

    assert steps[analytics.START] >= 1
    assert steps[analytics.AUTHOR_OPEN] >= 1
    assert steps[analytics.TIER_CHOSEN] >= 1
    assert steps[analytics.ORDER_SENT] >= 1


@pytest.mark.asyncio(loop_scope="module")
async def test_stats_shows_the_funnel(env):
    dp, bot, session, _ = env

    session.clear()
    await feed(dp, bot, make_message("/stats", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    report = session.texts()[0]
    assert "Воронка за 7 дн." in report
    assert "Зашли в бота" in report
    assert "Отправили заявку" in report


@pytest.mark.asyncio(loop_scope="module")
async def test_stats_accepts_a_period(env):
    dp, bot, session, _ = env

    session.clear()
    await feed(dp, bot, make_message("/stats 0", chat_id=ADMIN_CHAT_ID, user_id=ADMIN_ID), 1)

    assert any("всё время" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_daily_card_opens_from_the_menu_and_keeps_its_card(env):
    dp, bot, session, _ = env
    chat = 1501

    await feed(dp, bot, make_callback("daily:open", chat_id=chat), 1)
    first = session.texts("EditMessageText")[-1]

    session.clear()
    await feed(dp, bot, make_callback("daily:open", chat_id=chat), 2)
    again = session.texts("EditMessageText")[-1]

    assert "Карта дня" in first
    # Название карты — первая строка после заголовка; оно не должно поменяться
    assert first.split("\n")[2] == again.split("\n")[2]
    assert "уже вытянута" in again


@pytest.mark.asyncio(loop_scope="module")
async def test_subscription_turns_on_and_off(env):
    dp, bot, session, factory = env
    chat = 1502

    await feed(dp, bot, make_callback("daily:open", chat_id=chat), 1)
    await feed(dp, bot, make_callback("daily:on", chat_id=chat), 2)
    async with factory() as db:
        assert await Repo(db).is_daily_subscribed(chat) is True
    assert any("каждое утро" in text for text in session.texts())

    session.clear()
    await feed(dp, bot, make_callback("daily:off", chat_id=chat), 3)
    async with factory() as db:
        assert await Repo(db).is_daily_subscribed(chat) is False
    assert any("Больше не присылаю" in text for text in session.texts())


@pytest.mark.asyncio(loop_scope="module")
async def test_main_menu_offers_the_daily_card(env):
    dp, bot, session, _ = env

    session.clear()
    await feed(dp, bot, make_message("/start", chat_id=1503), 1)

    buttons = [
        button.callback_data
        for call in session.methods("SendMessage")
        if call.reply_markup
        for row in call.reply_markup.inline_keyboard
        for button in row
    ]
    assert "daily:open" in buttons


@pytest.mark.asyncio(loop_scope="module")
async def test_daily_card_image_is_sent_once_a_day(env):
    """Карта та же — значит и картинку второй раз слать незачем."""
    dp, bot, session, _ = env
    chat = 1504

    session.clear()
    await feed(dp, bot, make_callback("daily:open", chat_id=chat), 1)
    first_pass = len(session.methods("SendMediaGroup"))

    await feed(dp, bot, make_callback("daily:open", chat_id=chat), 2)
    await feed(dp, bot, make_callback("daily:open", chat_id=chat), 3)

    assert first_pass == 1
    assert len(session.methods("SendMediaGroup")) == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_reversed_card_is_sent_upside_down_and_cached_apart(env, monkeypatch):
    """Подпись говорит «перевёрнутая» — картинка должна говорить то же самое."""
    from aiogram.types import BufferedInputFile

    from bot.handlers.auto import send_card_images
    from bot.tarot import DrawnCard, get_card
    from bot.tarot.images import cache_key

    async def turned(card):
        return "перевёрнутая картинка".encode()

    monkeypatch.setattr("bot.handlers.auto.reversed_image_bytes", turned)

    dp, bot, session, factory = env
    card = get_card("major_18")
    drawn = [DrawnCard(position="Карта дня", card=card, reversed=True)]

    session.clear()
    async with factory() as db:
        # Обе записи — чужие: прямую картинку этой карты мог закэшировать соседний тест
        await forget_cached_image(db, cache_key(card, True))
        await forget_cached_image(db, cache_key(card, False))
        await send_card_images(bot, 1601, drawn, Repo(db), dp["settings"])

    media = session.methods("SendMediaGroup")[0].media
    assert isinstance(media[0].media, BufferedInputFile)

    async with factory() as db:
        cached = await Repo(db).card_file_ids([cache_key(card, True), cache_key(card, False)])
    # Отправка перевёрнутой карты заполняет только перевёрнутый ключ
    assert cache_key(card, True) in cached
    assert cache_key(card, False) not in cached


@pytest.mark.asyncio(loop_scope="module")
async def test_unrotatable_card_falls_back_without_poisoning_the_cache(env, monkeypatch):
    """Если повернуть не вышло, прямую картинку нельзя запомнить как перевёрнутую."""
    from bot.handlers.auto import send_card_images
    from bot.tarot import DrawnCard, get_card
    from bot.tarot.images import cache_key

    async def nothing(card):
        return None

    monkeypatch.setattr("bot.handlers.auto.reversed_image_bytes", nothing)

    dp, bot, session, factory = env
    card = get_card("major_17")
    drawn = [DrawnCard(position="Карта дня", card=card, reversed=True)]

    session.clear()
    async with factory() as db:
        await forget_cached_image(db, cache_key(card, True))
        await send_card_images(bot, 1602, drawn, Repo(db), dp["settings"])

    assert len(session.methods("SendMediaGroup")) == 1  # картинка всё же ушла
    async with factory() as db:
        cached = await Repo(db).card_file_ids([cache_key(card, True)])
    assert cached == {}
