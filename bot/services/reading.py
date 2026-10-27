"""Правила авто-расклада отдельно от того, куда он потом отправится.

Расклад скоро понадобится из двух мест: из чата и из мини-приложения. Правила
у них обязаны быть одни — лимиты, оплата, число, под которое ложатся карты, —
иначе через месяц окажется, что в приложении бесплатных раскладов больше, и
узнается это по выручке.

Поэтому здесь нет ни одного обращения к Telegram. Сценарий разбит надвое,
потому что между картами и разбором есть пауза в полминуты: карты показываются
сразу, разбор догоняет.

    решение = await start(...)      # доступ, лимит, карты
    итог = await finish(...)        # разбор, сохранение, списание
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import analytics, referrals
from ..access import Access
from ..access import check as check_access
from ..access import spend as spend_access
from ..config import Settings
from ..db import Repo
from ..profile import describe
from ..schedule import now
from ..tarot import DrawnCard, Spread, draw_spread, rng_for, serialize
from .interpreter import Interpretation, Interpreter


@dataclass(frozen=True)
class Refused:
    """Расклада не будет, и вот почему."""

    reason: str  # needs_daily | needs_payment | payment_not_ready | daily_limit
    free_left: int = 0
    limit: int = 0

    allowed = False


@dataclass(frozen=True)
class Finished:
    """Расклад состоялся: текст готов, оплата списана, награды розданы."""

    interpretation: Interpretation
    # Кому и сколько начислено за приглашение — сообщить об этом умеет только
    # тот, кто позвал: у чата есть переписка, у приложения — бот под рукой
    rewarded: tuple[int, int] | None = None


@dataclass(frozen=True)
class Started:
    """Карты легли. Разбора ещё нет."""

    drawn: list[DrawnCard]
    access: Access

    allowed = True


async def start(
    repo: Repo,
    settings: Settings,
    user_id: int,
    spread: Spread,
    question: str | None,
    *,
    number: int,
) -> Started | Refused:
    """Проверяет право на расклад и тянет карты. Ничего не сохраняет."""
    access = await check_access(repo, settings, user_id)
    if not access.allowed:
        await repo.log_event(user_id, analytics.PAYWALL, access.reason)
        reason = access.reason
        if reason == "needs_payment" and not settings.payment_provider_token:
            # Платить нечем: продавец не подключил приём денег
            reason = "payment_not_ready"
        return Refused(reason, access.free_left)

    if settings.daily_auto_limit > 0:
        used = await repo.count_readings_today(user_id)
        if used >= settings.daily_auto_limit:
            return Refused("daily_limit", access.free_left, settings.daily_auto_limit)

    # Потолок на бесплатные: пять раскладов — это три дня, а не один вечер.
    # Оплативший в него не упирается: заплатил — раскладывай сколько нужно
    if access.reason == "free" and settings.free_daily_limit > 0:
        today = await repo.count_readings_today(user_id)
        if today >= settings.free_daily_limit:
            return Refused("free_daily_limit", access.free_left, settings.free_daily_limit)

    # Карты ложатся под названное число, а не под случайность сервера:
    # тот же человек с тем же числом в ту же минуту получит тот же расклад
    drawn = draw_spread(
        spread,
        allow_reversed=settings.allow_reversed,
        reversed_chance=settings.reversed_chance,
        rng=rng_for(user_id, number, now(settings), question),
    )
    return Started(drawn=drawn, access=access)


async def finish(
    repo: Repo,
    interpreter: Interpreter,
    user_id: int,
    spread: Spread,
    question: str | None,
    started: Started,
    settings: Settings | None = None,
) -> Finished:
    """Пишет разбор, сохраняет расклад и только теперь списывает оплату.

    Здесь же начисляется награда пригласившему: первый расклад приглашённого
    бывает один, и платить за него должно одно место — иначе чат и приложение
    заплатят каждый свой раз.
    """
    about = describe(await repo.get_user(user_id))
    result = await interpreter.interpret(spread, started.drawn, question, about=about)

    await repo.save_reading(
        user_id=user_id,
        spread_key=spread.key,
        question=question,
        cards=serialize(started.drawn),
        interpretation=result.text,
        ai_used=result.ai_used,
    )
    await repo.log_event(user_id, analytics.AUTO_READING, spread.key)
    # Списываем только теперь: если расклад не состоялся, оплата остаётся
    await spend_access(repo, started.access)

    rewarded = None
    if settings is not None:
        rewarded = await referrals.reward(repo, settings, user_id)
    return Finished(interpretation=result, rewarded=rewarded)
