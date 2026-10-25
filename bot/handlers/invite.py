"""Приглашения и промокоды.

Ссылка у каждого своя, награда — бесплатные расклады. Сами правила живут в
bot/referrals.py и bot/promo.py: отсюда их зовут, здесь же остаётся то, что
принадлежит чату, — какими словами сказать.
"""

from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import promo as promo_rules
from .. import texts
from ..access import check as check_access
from ..config import Settings
from ..db import Repo
from ..filters import IsAdmin
from ..keyboards import back_to_menu, invite_menu
from ..referrals import link_for
from ..states import PromoFlow

logger = logging.getLogger(__name__)

router = Router(name="invite")


async def show_invite(message: Message, repo: Repo, settings: Settings, user_id: int) -> None:
    bot_user = await message.bot.me()
    access = await check_access(repo, settings, user_id)
    await message.answer(
        texts.INVITE.format(
            link=link_for(bot_user.username or "", user_id),
            bonus=settings.referral_bonus,
            welcome=settings.referral_welcome,
            invited=await repo.invited_count(user_id),
            # Владельцу лимит не считают, и показывать ему ноль было бы ложью
            left="без ограничений" if access.reason == "owner" else access.free_left,
        ),
        reply_markup=invite_menu(),
        disable_web_page_preview=True,
    )


@router.message(Command("invite"))
async def cmd_invite(message: Message, repo: Repo, settings: Settings) -> None:
    await show_invite(message, repo, settings, message.chat.id)


@router.callback_query(F.data == "menu:invite")
async def cb_invite(call: CallbackQuery, repo: Repo, settings: Settings) -> None:
    if isinstance(call.message, Message):
        await show_invite(call.message, repo, settings, call.from_user.id)
    await call.answer()


async def tell_the_inviter(message: Message, inviter_id: int, bonus: int) -> None:
    """Сообщает пригласившему о награде. Молчит, если тот закрыл переписку."""
    try:
        await message.bot.send_message(inviter_id, texts.INVITE_REWARDED.format(bonus=bonus))
    except TelegramAPIError as exc:
        logger.info("Пригласившему %s не написать: %s", inviter_id, exc)


# --- Промокоды -----------------------------------------------------------


@router.callback_query(F.data == "promo:enter")
async def cb_promo(call: CallbackQuery, state: FSMContext) -> None:
    if isinstance(call.message, Message):
        await state.set_state(PromoFlow.waiting_code)
        await call.message.answer(texts.PROMO_ASK)
    await call.answer()


@router.message(Command("promo"))
async def cmd_promo(
    message: Message, command: CommandObject, state: FSMContext, repo: Repo
) -> None:
    """Код можно прислать сразу за командой, можно следующим сообщением."""
    if command.args:
        await apply(message, repo, command.args)
        return
    await state.set_state(PromoFlow.waiting_code)
    await message.answer(texts.PROMO_ASK)


@router.message(PromoFlow.waiting_code)
async def got_code(message: Message, state: FSMContext, repo: Repo) -> None:
    await state.clear()
    await apply(message, repo, message.text or "")


async def apply(message: Message, repo: Repo, code: str) -> None:
    result = await promo_rules.redeem(repo, message.chat.id, code)
    if result.ok:
        await message.answer(
            texts.PROMO_APPLIED.format(readings=result.readings, total=result.total),
            reply_markup=back_to_menu(),
        )
        return
    await message.answer(
        texts.PROMO_REFUSED.get(result.reason, texts.PROMO_REFUSED["unknown"]),
        reply_markup=back_to_menu(),
    )


# --- Промокоды: сторона владельца ----------------------------------------


@router.message(Command("promos"), IsAdmin())
async def cmd_promos(message: Message, repo: Repo) -> None:
    codes = await repo.promos()
    if not codes:
        await message.answer(texts.PROMO_LIST_EMPTY)
        return

    lines = []
    for code in codes:
        limit = f"{code.used}/{code.uses_limit}" if code.uses_limit else f"{code.used}/∞"
        until = f", до {code.expires_at}" if code.expires_at else ""
        mark = "" if code.active else " (выключен)"
        lines.append(f"<code>{code.code}</code> — {code.readings} раскл., {limit}{until}{mark}")
    await message.answer("\n".join(lines))


@router.message(Command("promo_new"), IsAdmin())
async def cmd_promo_new(message: Message, command: CommandObject, repo: Repo) -> None:
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(texts.PROMO_NEW_HELP)
        return

    code = promo_rules.normalize(parts[0])
    readings = int(parts[1])
    uses_limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    expires: date | None = None
    if len(parts) > 3:
        try:
            expires = date.fromisoformat(parts[3])
        except ValueError:
            await message.answer(texts.PROMO_NEW_HELP)
            return

    if await repo.promo_by_code(code) is not None:
        await message.answer(texts.PROMO_EXISTS)
        return

    await repo.add_promo(code, readings, uses_limit, expires)
    await message.answer(
        texts.PROMO_CREATED.format(
            code=code,
            readings=readings,
            limit=f"лимит {uses_limit}" if uses_limit else "без лимита",
            until=f"до {expires}" if expires else "без срока",
        )
    )
