"""Как карты попадают в чат.

Отправка картинок — не часть сценария расклада: ею пользуются и расклад, и
карта дня, и утренняя рассылка, и повторно открытый расклад из истории. Пока
она лежала внутри обработчика, рассылка добраться до неё не могла — и утренняя
карта приходила текстом, без карты.

Присланный Telegram file_id сохраняется, поэтому к источнику бот идёт один раз
на карту: дальше та же картинка уходит мгновенно и не стоит ничего.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaPhoto

from .. import texts
from ..config import Settings
from ..db import Repo
from ..tarot import DrawnCard
from ..tarot.images import cache_key, commons_url, local_path, reversed_image_bytes
from ..tarot.layout import sheet

logger = logging.getLogger(__name__)


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
