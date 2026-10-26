"""Карта дня: одна на сутки, рассылка раз в день и без повторов после перезапуска."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramForbiddenError

from bot import analytics
from bot.config import Settings
from bot.db import create_engine, create_session_factory, init_models
from bot.db.repo import Repo
from bot.services.daily import DailyCards, card_message, card_of_the_day

MSK = ZoneInfo("Europe/Moscow")
TODAY = date(2026, 10, 12)


def settings(**overrides) -> Settings:
    return Settings(BOT_TOKEN="t:t", _env_file=None, **overrides)


class FakeBot:
    """Запоминает отправленное; умеет изображать блокировку и поломку."""

    def __init__(
        self,
        fail_for: set[int] | None = None,
        blocked: set[int] | None = None,
        no_photos: bool = False,
    ) -> None:
        self.sent: list[tuple[int, str]] = []
        self.photos: list[int] = []
        self.fail_for = fail_for or set()
        self.blocked = blocked or set()
        self.no_photos = no_photos

    async def send_media_group(self, chat_id: int, media, **kwargs):
        if self.no_photos:
            raise RuntimeError("картинка не ушла")
        self.photos.append(chat_id)
        # Без photo в ответе кэш file_id не трогается — здесь это и не нужно
        return [SimpleNamespace(photo=None) for _ in media]

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id in self.fail_for:
            raise RuntimeError("Telegram прилёг")
        self.sent.append((chat_id, text))

    @property
    def recipients(self) -> list[int]:
        return [chat_id for chat_id, _ in self.sent]


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'daily.db'}")
    await init_models(engine)
    yield create_session_factory(engine)
    await engine.dispose()


async def subscribe(factory, *user_ids: int) -> None:
    async with factory() as session:
        repo = Repo(session)
        for user_id in user_ids:
            await repo.upsert_user(user_id, None, "Клиент")
            await repo.set_daily_subscription(user_id, True)


@pytest.mark.asyncio
async def test_card_stays_the_same_all_day(factory):
    """Иначе «карта дня» — это кнопка «вытянуть ещё раз»."""
    async with factory() as session:
        repo = Repo(session)
        first = await card_of_the_day(repo, settings(), 1, TODAY)
        again = [await card_of_the_day(repo, settings(), 1, TODAY) for _ in range(5)]

    assert all(card.card.id == first.card.id for card in again)
    assert all(card.reversed == first.reversed for card in again)


@pytest.mark.asyncio
async def test_a_new_day_brings_a_new_draw(factory):
    async with factory() as session:
        repo = Repo(session)
        await card_of_the_day(repo, settings(), 1, TODAY)
        await card_of_the_day(repo, settings(), 1, date(2026, 10, 13))
        stored = [await repo.daily_card(1, TODAY), await repo.daily_card(1, date(2026, 10, 13))]

    assert all(card is not None for card in stored)


@pytest.mark.asyncio
async def test_each_person_gets_their_own_record(factory):
    """Карты у двоих могут совпасть случайно, а записи — нет."""
    async with factory() as session:
        repo = Repo(session)
        await card_of_the_day(repo, settings(), 1, TODAY)
        await card_of_the_day(repo, settings(), 2, TODAY)

        mine = await repo.daily_card(1, TODAY)
        theirs = await repo.daily_card(2, TODAY)

    assert mine is not None and theirs is not None
    assert mine.id != theirs.id


@pytest.mark.asyncio
async def test_broadcast_reaches_subscribers_once(factory):
    await subscribe(factory, 1, 2, 3)
    bot = FakeBot()
    sender = DailyCards(bot, settings(), factory)

    assert await sender.send_due(TODAY) == 3
    assert sorted(bot.recipients) == [1, 2, 3]

    # Повторный проход — например, после перезапуска бота — ничего не шлёт.
    assert await sender.send_due(TODAY) == 0
    assert len(bot.sent) == 3


@pytest.mark.asyncio
async def test_unsubscribed_people_get_nothing(factory):
    await subscribe(factory, 1, 2)
    async with factory() as session:
        await Repo(session).set_daily_subscription(2, False)

    bot = FakeBot()
    await DailyCards(bot, settings(), factory).send_due(TODAY)

    assert bot.recipients == [1]


@pytest.mark.asyncio
async def test_blocking_the_bot_unsubscribes_instead_of_retrying_daily(factory):
    await subscribe(factory, 1, 2)
    bot = FakeBot(blocked={1})

    assert await DailyCards(bot, settings(), factory).send_due(TODAY) == 1

    async with factory() as session:
        repo = Repo(session)
        assert await repo.is_daily_subscribed(1) is False
        assert await repo.is_daily_subscribed(2) is True


@pytest.mark.asyncio
async def test_one_failed_delivery_does_not_stop_the_rest(factory):
    await subscribe(factory, 1, 2, 3)
    bot = FakeBot(fail_for={2})

    sent = await DailyCards(bot, settings(), factory).send_due(TODAY)

    assert sent == 2
    assert sorted(bot.recipients) == [1, 3]


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_next_time(factory):
    """Не отмечаем доставленной карту, которая не ушла."""
    await subscribe(factory, 1)
    broken = FakeBot(fail_for={1})
    await DailyCards(broken, settings(), factory).send_due(TODAY)

    working = FakeBot()
    assert await DailyCards(working, settings(), factory).send_due(TODAY) == 1


@pytest.mark.asyncio
async def test_nothing_goes_out_before_the_appointed_hour(factory, monkeypatch):
    await subscribe(factory, 1)
    bot = FakeBot()
    sender = DailyCards(bot, settings(DAILY_CARD_HOUR=9), factory)

    monkeypatch.setattr(
        "bot.services.daily.now", lambda _: datetime(2026, 10, 12, 7, 30, tzinfo=MSK)
    )
    assert await sender.tick() == 0
    assert bot.sent == []

    monkeypatch.setattr(
        "bot.services.daily.now", lambda _: datetime(2026, 10, 12, 9, 5, tzinfo=MSK)
    )
    assert await sender.tick() == 1


@pytest.mark.asyncio
async def test_batch_limits_one_pass(factory):
    await subscribe(factory, *range(1, 11))
    bot = FakeBot()
    sender = DailyCards(bot, settings(DAILY_CARD_BATCH=4), factory)

    assert await sender.send_due(TODAY) == 4
    assert await sender.send_due(TODAY) == 4
    assert await sender.send_due(TODAY) == 2
    assert await sender.send_due(TODAY) == 0


@pytest.mark.asyncio
async def test_delivery_is_recorded_as_an_event(factory):
    await subscribe(factory, 1)
    await DailyCards(FakeBot(), settings(), factory).send_due(TODAY)

    async with factory() as session:
        counted = await Repo(session).funnel((analytics.DAILY_SENT,))

    assert counted[analytics.DAILY_SENT] == 1


@pytest.mark.asyncio
async def test_message_says_what_the_card_means(factory):
    async with factory() as session:
        drawn = await card_of_the_day(Repo(session), settings(), 1, TODAY)

    text = card_message(drawn)

    assert "Карта дня" in text
    assert drawn.meaning[:20] in text


@pytest.mark.asyncio
async def test_the_morning_card_arrives_as_a_picture(factory):
    """Карта дня без карты — это не карта дня, а сообщение о ней."""
    await subscribe(factory, 1)
    bot = FakeBot()

    assert await DailyCards(bot, settings(), factory).send_due(TODAY) == 1

    assert bot.photos == [1]
    assert bot.recipients == [1]  # и текст тоже пришёл


@pytest.mark.asyncio
async def test_a_missing_picture_does_not_cancel_the_card(factory):
    """Картинка — украшение: если она не ушла, слова всё равно должны дойти."""
    await subscribe(factory, 1)
    bot = FakeBot(no_photos=True)

    assert await DailyCards(bot, settings(), factory).send_due(TODAY) == 1

    assert bot.photos == []
    assert bot.recipients == [1]
