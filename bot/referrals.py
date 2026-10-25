"""Кто кого привёл и что за это полагается.

Награда — бесплатные расклады обоим: пригласившему и пришедшему. Денег это не
касается, поэтому работает одинаково во всех ботах франшизы и не требует ни
баланса, ни вывода.

Момент начисления выбран не случайно. Пришедшему расклад начисляется сразу —
это его первое впечатление. Пригласившему — только когда приглашённый сделал
свой первый расклад: за нажатие «старт» платить нельзя, иначе ссылку прогонят
по мёртвым аккаунтам и бесплатные расклады кончатся раньше клиентов.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import analytics
from .config import Settings
from .db import Repo

# Хвост ссылки: t.me/бот?start=ref123456
PREFIX = "ref"


def payload_for(user_id: int) -> str:
    return f"{PREFIX}{user_id}"


def link_for(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start={payload_for(user_id)}"


def inviter_from(payload: str | None) -> int | None:
    """Номер пригласившего из хвоста ссылки, если он там есть."""
    if not payload or not payload.startswith(PREFIX):
        return None
    tail = payload[len(PREFIX) :]
    return int(tail) if tail.isdigit() else None


@dataclass(frozen=True)
class Joined:
    """Чем закончился приход по ссылке."""

    inviter_id: int
    welcome: int  # сколько раскладов начислено пришедшему


async def join(
    repo: Repo, settings: Settings, user_id: int, payload: str | None
) -> Joined | None:
    """Запоминает пригласившего и выдаёт подарок пришедшему.

    Ничего не делает, если ссылка своя собственная, если человек уже заходил
    раньше или если пригласившего в этом боте не существует: у каждого бренда
    свои клиенты, и чужой номер здесь ничего не значит.
    """
    inviter_id = inviter_from(payload)
    if inviter_id is None or inviter_id == user_id:
        return None
    if await repo.get_user(inviter_id) is None:
        return None
    if not await repo.remember_inviter(user_id, inviter_id):
        return None

    welcome = max(0, settings.referral_welcome)
    if welcome:
        await repo.add_bonus(user_id, welcome)
    await repo.log_event(user_id, analytics.REFERRAL_JOIN, str(inviter_id))
    return Joined(inviter_id=inviter_id, welcome=welcome)


async def reward(repo: Repo, settings: Settings, user_id: int) -> tuple[int, int] | None:
    """Награждает пригласившего за первый расклад приглашённого.

    Возвращает номер пригласившего и сколько ему начислено — чтобы тот, кто
    вызвал, мог об этом сообщить. Второй раз по тому же человеку не сработает:
    первый расклад бывает один.
    """
    user = await repo.get_user(user_id)
    if user is None or user.invited_by is None:
        return None
    if await repo.count_auto_readings(user_id) != 1:
        return None

    bonus = max(0, settings.referral_bonus)
    if bonus:
        await repo.add_bonus(user.invited_by, bonus)
    await repo.log_event(user.invited_by, analytics.REFERRAL_REWARD, str(user_id))
    return user.invited_by, bonus
