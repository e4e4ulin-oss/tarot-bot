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
from aiogram.types import CallbackQuery, Chat, MessageId, Update, User
from aiogram.types import Message as TgMessage

from bot.__main__ import build_dispatcher
from bot.config import Settings
from bot.db import OrderStatus, Repo, create_engine, create_session_factory, init_models
from bot.services import GrokClient, Interpreter

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
    )
    engine = create_engine(settings.database_url)
    await init_models(engine)
    factory = create_session_factory(engine)

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

    # Анастасия отвечает из админ-чата
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
