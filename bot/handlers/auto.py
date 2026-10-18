"""Автоматический режим: бот тасует колоду и сам пишет разбор."""

from __future__ import annotations

import logging
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from .. import analytics, texts
from ..access import check as check_access
from ..access import spend as spend_access
from ..config import Settings
from ..db import Repo
from ..keyboards import (
    after_reading_menu,
    number_menu,
    paywall_menu,
    question_menu,
    spreads_menu,
    subscribe_menu,
)
from ..services.interpreter import DISCLAIMER, Interpreter
from ..states import AutoFlow
from ..profile import describe
from ..schedule import now
from ..tarot import (
    SPREADS,
    DrawnCard,
    Spread,
    draw_spread,
    format_cards,
    get_spread,
    rng_for,
    serialize,
)
from ..tarot.images import cache_key, commons_url, local_path, reversed_image_bytes
from ..tarot.layout import sheet
from ..utils import split_text

logger = logging.getLogger(__name__)

# Числа крупнее не нужны: это жест, а не счёт
NUMBER_LIMIT = 99

router = Router(name="auto")


@router.callback_query(F.data == "menu:auto")
async def cb_spreads(call: CallbackQuery, state: FSMContext, repo: Repo) -> None:
    await state.clear()
    await repo.log_event(call.from_user.id, analytics.AUTO_OPEN)
    if isinstance(call.message, Message):
        await call.message.edit_text(texts.CHOOSE_SPREAD, reply_markup=spreads_menu())
    await call.answer()


@router.callback_query(F.data.startswith("spread:"))
async def cb_spread_chosen(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    spread = SPREADS.get(key)
    if spread is None:
        await call.answer(texts.ERROR, show_alert=True)
        return

    await state.set_state(AutoFlow.waiting_question)
    await state.update_data(spread_key=key)

    template = texts.ASK_QUESTION_YES_NO if spread.yes_no else texts.ASK_QUESTION
    if isinstance(call.message, Message):
        await call.message.edit_text(
            template.format(spread=spread.title, subtitle=spread.subtitle),
            reply_markup=question_menu(key, allow_skip=not spread.yes_no),
        )
    await call.answer()


@router.callback_query(F.data.startswith("draw:"))
async def cb_draw_without_question(
    call: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    key = call.data.split(":", 1)[1]
    spread = SPREADS.get(key)
    if spread is None:
        await call.answer(texts.ERROR, show_alert=True)
        return
    await state.set_state(AutoFlow.waiting_number)
    await state.update_data(spread_key=key, question=None)
    await call.answer()
    if isinstance(call.message, Message):
        await call.message.answer(texts.ASK_NUMBER, reply_markup=number_menu(key))


@router.message(AutoFlow.waiting_question, F.text)
async def on_question(
    message: Message,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    data = await state.get_data()
    spread = get_spread(data.get("spread_key", "ptf"))
    question = (message.text or "").strip()

    if len(question) > settings.max_question_length:
        await message.answer(texts.QUESTION_TOO_LONG.format(limit=settings.max_question_length))
        return

    await state.set_state(AutoFlow.waiting_number)
    await state.update_data(spread_key=spread.key, question=question)
    await message.answer(texts.ASK_NUMBER, reply_markup=number_menu(spread.key))


@router.message(AutoFlow.waiting_question)
async def on_non_text_question(message: Message) -> None:
    await message.answer(texts.QUESTION_REQUIRED)


@router.message(AutoFlow.waiting_number, F.text)
async def on_number(
    message: Message,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= NUMBER_LIMIT:
        await message.answer(texts.BAD_NUMBER)
        return

    data = await state.get_data()
    spread = get_spread(data.get("spread_key", "ptf"))
    await state.clear()
    await run_reading(
        message,
        message.from_user.id,
        spread,
        data.get("question"),
        repo,
        settings,
        interpreter,
        number=int(raw),
    )


@router.callback_query(AutoFlow.waiting_number, F.data.startswith("number:"))
async def cb_number_from_the_deck(
    call: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
) -> None:
    """Человек может не называть число — тогда его называет колода."""
    data = await state.get_data()
    spread = get_spread(data.get("spread_key", "ptf"))
    question = data.get("question")
    await state.clear()
    await call.answer()
    if isinstance(call.message, Message):
        await run_reading(
            call.message,
            call.from_user.id,
            spread,
            question,
            repo,
            settings,
            interpreter,
            number=secrets.randbelow(NUMBER_LIMIT) + 1,
        )


async def send_card_images(
    bot: Bot, chat_id: int, drawn: list[DrawnCard], repo: Repo, settings: Settings
) -> None:
    """Показывает сами карты. Своя картинка приоритетнее скана, ссылка — на крайний случай.

    Присланный Telegram file_id сохраняется, поэтому к источнику бот идёт один раз на карту.
    Картинки — украшение: любая неудача здесь не должна мешать раскладу.
    """
    if not settings.send_card_images:
        return

    # Большой расклад уходит одним листом: пять отдельных фотографий — это уже
    # лента, в которой не видно ни порядка, ни расклада целиком
    if len(drawn) > settings.image_cards_limit:
        await send_spread_sheet(bot, chat_id, drawn)
        return

    cached = await repo.card_file_ids(
        [cache_key(item.card, item.reversed) for item in drawn]
    )
    prepared = [await _card_photo(item, cached) for item in drawn]

    try:
        sent = await bot.send_media_group(chat_id, [photo for _, photo in prepared])
    except TelegramAPIError as exc:
        logger.warning("Картинки карт отправить не удалось: %s", exc)
        return

    for (key, _), sent_message in zip(prepared, sent, strict=False):
        # key is None, когда перевернуть не удалось: кэшировать прямую картинку
        # под перевёрнутым ключом нельзя, иначе она застрянет там навсегда.
        if key is not None and sent_message.photo:
            await repo.save_card_file_id(key, sent_message.photo[-1].file_id)


async def send_spread_sheet(bot: Bot, chat_id: int, drawn: list[DrawnCard]) -> None:
    """Расклад одной картинкой: карты по порядку, с номерами.

    Лист собирается каждый раз заново — сочетание карт своё у каждого
    расклада, и кэшировать его незачем.
    """
    picture = await sheet(drawn)
    if picture is None:
        logger.warning("Лист расклада не собрался: картинок карт не досталось")
        return

    try:
        await bot.send_photo(
            chat_id,
            BufferedInputFile(picture, filename="spread.jpg"),
            caption=texts.SPREAD_SHEET,
        )
    except TelegramAPIError as exc:
        logger.warning("Лист расклада отправить не удалось: %s", exc)


async def _card_photo(
    item: DrawnCard, cached: dict[str, str]
) -> tuple[str | None, InputMediaPhoto]:
    """Картинка карты и ключ кэша для неё (None — не кэшировать)."""
    key = cache_key(item.card, item.reversed)
    if (file_id := cached.get(key)) is not None:
        return key, InputMediaPhoto(media=file_id, caption=item.title)

    if item.reversed:
        turned = await reversed_image_bytes(item.card)
        if turned is not None:
            return key, InputMediaPhoto(
                media=BufferedInputFile(turned, filename=f"{item.card.id}_rev.jpg"),
                caption=item.title,
            )
        # Не вышло повернуть — прямая картинка лучше, чем пустота, но она не наша
        key = None

    if (path := local_path(item.card)) is not None:
        source: str | FSInputFile = FSInputFile(path)
    else:
        source = commons_url(item.card)
    return key, InputMediaPhoto(media=source, caption=item.title)


async def run_reading(
    message: Message,
    user_id: int,
    spread: Spread,
    question: str | None,
    repo: Repo,
    settings: Settings,
    interpreter: Interpreter,
    *,
    number: int,
) -> None:
    """Общий сценарий: доступ → лимит → карты → разбор → сохранение."""
    access = await check_access(repo, settings, user_id)
    if not access.allowed:
        await repo.log_event(user_id, analytics.PAYWALL, access.reason)
        if access.reason == "needs_daily":
            await message.answer(texts.NEEDS_DAILY, reply_markup=subscribe_menu())
        elif not settings.payment_provider_token:
            await message.answer(texts.PAYMENT_NOT_READY.format(author=settings.author_name))
        else:
            await message.answer(
                texts.FREE_SPENT.format(
                    total=settings.free_auto_readings,
                    price=settings.auto_reading_price,
                    currency=settings.author_currency,
                ),
                reply_markup=paywall_menu(settings.auto_reading_price, settings.author_currency),
            )
        return

    if settings.daily_auto_limit > 0:
        used = await repo.count_readings_today(user_id)
        if used >= settings.daily_auto_limit:
            await message.answer(
                texts.DAILY_LIMIT.format(
                    limit=settings.daily_auto_limit, author=settings.author_name
                ),
                reply_markup=after_reading_menu(settings.author_name),
            )
            return

    # Карты ложатся под названное число, а не под случайность сервера:
    # тот же человек с тем же числом в ту же минуту получит тот же расклад
    drawn = draw_spread(
        spread,
        allow_reversed=settings.allow_reversed,
        reversed_chance=settings.reversed_chance,
        rng=rng_for(user_id, number, now(settings), question),
    )
    about = describe(await repo.get_user(user_id))

    await send_card_images(message.bot, message.chat.id, drawn, repo, settings)
    cards_message = await message.answer(format_cards(spread, drawn, question))
    status = await message.answer(texts.INTERPRETING)

    # «печатает…» в чате, пока модель думает — иначе кажется, что бот завис
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        result = await interpreter.interpret(spread, drawn, question, about=about)

    try:
        await status.delete()
    except TelegramBadRequest:  # pragma: no cover - сообщение могли удалить вручную
        logger.debug("Не удалось удалить служебное сообщение")

    body = result.text if result.ai_used else f"{texts.FALLBACK_NOTE}\n\n{result.text}"
    chunks = split_text(body)
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        await cards_message.answer(
            chunk + (f"\n\n<i>{DISCLAIMER}</i>" if is_last else ""),
            reply_markup=after_reading_menu(settings.author_name) if is_last else None,
        )

    await repo.save_reading(
        user_id=user_id,
        spread_key=spread.key,
        question=question,
        cards=serialize(drawn),
        interpretation=result.text,
        ai_used=result.ai_used,
    )
    await repo.log_event(user_id, analytics.AUTO_READING, spread.key)
    # Списываем только теперь: если расклад не состоялся, оплата остаётся
    await spend_access(repo, access)
