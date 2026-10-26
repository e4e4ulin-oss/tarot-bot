"""Напоминание о карте дня тем, кто зашёл и остановился.

Бесплатные расклады открывает подписка на утреннюю карту. Человек, который
просто не знает об этом условии, выглядит точно как человек, которому оно не
нужно, — поэтому о нём напоминают.

Главное здесь не «как отправить», а «когда молчать»:
— не раньше чем через несколько часов после первого прихода: человек ещё
  смотрит сам, и напоминание в спину только мешает;
— не чаще раза в неделю;
— не больше трёх раз за всё время. Кто не подписался после третьего, не
  подпишется и после десятого, а вот заблокирует бота — запросто;
— и сразу прекращая, как только подписка появилась.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from .. import analytics, texts
from ..config import Settings
from ..db import Repo
from ..keyboards import subscribe_menu
from ..schedule import now

logger = logging.getLogger(__name__)


class Nudges:
    """Фоновое напоминание. Один процесс бота — один экземпляр на арендатора."""

    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        session_factory,
        *,
        tenant_id: int = 1,
        interval: float = 600.0,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.session_factory = session_factory
        self.tenant_id = tenant_id
        self.interval = interval

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # напоминание не должно ронять бота
                logger.exception("Напоминание о карте дня сорвалось")
            await asyncio.sleep(self.interval)

    async def tick(self) -> int:
        if not self.settings.nudge_enabled:
            return 0
        moment = now(self.settings)
        # Один час в сутки: днём, а не ночью и не вместе с утренней рассылкой
        if moment.hour != self.settings.nudge_hour:
            return 0
        return await self.send_due(moment.replace(tzinfo=None))

    async def send_due(self, moment: datetime) -> int:
        async with self.session_factory() as session:
            repo = Repo(session, self.tenant_id)
            waiting = await repo.nudge_candidates(
                older_than=moment - timedelta(hours=self.settings.nudge_after_hours),
                quiet_until=moment - timedelta(days=self.settings.nudge_every_days),
                limit=self.settings.nudge_batch,
                max_nudges=self.settings.nudge_max,
            )
            sent = 0
            for user in waiting:
                if await self._send_one(repo, user, moment):
                    sent += 1
            return sent

    async def _send_one(self, repo: Repo, user, moment: datetime) -> bool:
        try:
            await self.bot.send_message(
                user.id,
                texts.NUDGE_DAILY.format(free=self.settings.free_auto_readings),
                reply_markup=subscribe_menu(),
            )
        except TelegramForbiddenError:
            # Заблокировал бота — напоминать больше некому и незачем
            logger.info("Напоминание: %s заблокировал бота", user.id)
            await repo.mark_nudged(user, moment)
            return False
        except TelegramRetryAfter as exc:
            logger.warning("Напоминание: Telegram просит подождать %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            return False
        except Exception as exc:  # одно непрошедшее не ломает остальные
            logger.warning("Напоминание не доставлено %s: %s", user.id, exc)
            return False

        await repo.mark_nudged(user, moment)
        await repo.log_event(user.id, analytics.NUDGE_SENT)
        return True
