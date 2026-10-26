"""Карта дня: одна карта на человека в сутки и утренняя рассылка подписчикам.

Карта фиксируется на день: если бы она менялась при каждом открытии, это была бы
не карта дня, а кнопка «вытянуть ещё раз». Рассылка идёт раз в сутки и помнит,
кому уже отправила, поэтому перезапуск бота не рассылает карты повторно.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from .. import analytics, texts
from ..config import Settings
from ..db import Repo
from ..keyboards import daily_card_menu
from ..schedule import now
from ..tarot import SPREADS, DrawnCard, draw_spread, get_card
from .cards import send_card_images

logger = logging.getLogger(__name__)


async def card_of_the_day(repo: Repo, settings: Settings, user_id: int, day: date) -> DrawnCard:
    """Сегодняшняя карта человека: уже вытянутая или новая."""
    stored = await repo.daily_card(user_id, day)
    if stored is not None:
        return DrawnCard(
            position=SPREADS["day"].positions[0],
            card=get_card(stored.card_id),
            reversed=stored.reversed,
        )

    drawn = draw_spread(
        SPREADS["day"],
        allow_reversed=settings.allow_reversed,
        reversed_chance=settings.reversed_chance,
    )[0]
    await repo.save_daily_card(user_id, day, drawn.card.id, drawn.reversed)
    return drawn


def card_message(drawn: DrawnCard) -> str:
    keywords = ", ".join(drawn.keywords)
    return texts.DAILY_CARD.format(
        title=texts.esc(drawn.title),
        meaning=texts.esc(drawn.meaning),
        keywords=texts.esc(keywords),
    )


class DailyCards:
    """Фоновая рассылка. Один процесс бота — один экземпляр."""

    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        session_factory,
        *,
        tenant_id: int = 1,
        interval: float = 60.0,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.session_factory = session_factory
        # Рассылка идёт подписчикам своего бренда, а не всем подряд
        self.tenant_id = tenant_id
        self.interval = interval

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # рассылка не должна ронять бота
                logger.exception("Рассылка карты дня сорвалась")
            await asyncio.sleep(self.interval)

    async def tick(self) -> int:
        moment = now(self.settings)
        # Раньше назначенного часа не шлём; позже — шлём, опоздать лучше, чем пропустить.
        if moment.hour < self.settings.daily_card_hour:
            return 0
        return await self.send_due(moment.date())

    async def send_due(self, day: date) -> int:
        async with self.session_factory() as session:
            repo = Repo(session, self.tenant_id)
            waiting = await repo.daily_subscribers_pending(day, self.settings.daily_card_batch)
            sent = 0
            for user_id in waiting:
                if await self._send_one(repo, user_id, day):
                    sent += 1
            return sent

    async def _send_one(self, repo: Repo, user_id: int, day: date) -> bool:
        drawn = await card_of_the_day(repo, self.settings, user_id, day)
        try:
            # Сначала сама карта, потом слова о ней: утром человек смотрит,
            # а читает уже потом. Раньше карта дня приходила без карты
            try:
                await send_card_images(self.bot, user_id, [drawn], repo, self.settings)
            except Exception as exc:  # noqa: BLE001 - картинка не отменяет рассылку
                logger.warning("Карта дня: картинку для %s не отправить: %s", user_id, exc)
            await self.bot.send_message(
                user_id, card_message(drawn), reply_markup=daily_card_menu(subscribed=True)
            )
        except TelegramForbiddenError:
            # Человек заблокировал бота: отписываем, чтобы не долбиться каждый день.
            logger.info("Карта дня: %s заблокировал бота, отписываю", user_id)
            await repo.set_daily_subscription(user_id, False)
            return False
        except TelegramRetryAfter as exc:
            logger.warning("Карта дня: Telegram просит подождать %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            return False
        except Exception as exc:  # доставка одному не должна ломать рассылку остальным
            logger.warning("Карта дня не доставлена %s: %s", user_id, exc)
            return False

        card = await repo.daily_card(user_id, day)
        if card is not None:
            await repo.mark_daily_delivered(card)
        await repo.log_event(user_id, analytics.DAILY_SENT)
        return True
