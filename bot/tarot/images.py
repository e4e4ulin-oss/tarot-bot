"""Изображения карт.

Источник по умолчанию — сканы колоды Райдера-Уэйта с Викисклада (рисунки Памелы
Колман Смит, 1909; общественное достояние). Файлы не хранятся в репозитории:
Telegram скачивает картинку по ссылке сам, а полученный file_id бот кэширует,
поэтому к Викискладу он обращается один раз на карту.

Перевёрнутые карты показываются перевёрнутой картинкой: иначе подпись говорит одно,
а изображение другое. Для этого файл нужно скачать и повернуть у себя, поэтому
перевёрнутый вариант кэшируется отдельным ключом `<id>:rev`.

Если рядом лежит своя картинка — `bot/tarot/data/cards/<id>.jpg` — используется она.
Так можно постепенно заменить сканы фотографиями собственной колоды,
не трогая код: имя файла совпадает с id карты (major_00, cups_01, wands_14…).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from .deck import Card

logger = logging.getLogger(__name__)

LOCAL_DIR = Path(__file__).parent / "data" / "cards"
LOCAL_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

COMMONS_BASE = "https://upload.wikimedia.org/wikipedia/commons"

_MAJOR_SLUGS = (
    "00_Fool",
    "01_Magician",
    "02_High_Priestess",
    "03_Empress",
    "04_Emperor",
    "05_Hierophant",
    "06_Lovers",
    "07_Chariot",
    "08_Strength",
    "09_Hermit",
    "10_Wheel_of_Fortune",
    "11_Justice",
    "12_Hanged_Man",
    "13_Death",
    "14_Temperance",
    "15_Devil",
    "16_Tower",
    "17_Star",
    "18_Moon",
    "19_Sun",
    "20_Judgement",
    "21_World",
)

_SUIT_PREFIX = {"cups": "Cups", "wands": "Wands", "swords": "Swords", "pentacles": "Pents"}


def commons_filename(card: Card) -> str:
    """Имя файла на Викискладе для этой карты."""
    if card.is_major:
        return f"RWS_Tarot_{_MAJOR_SLUGS[card.number]}.jpg"
    return f"{_SUIT_PREFIX[card.suit or '']}{card.number:02d}.jpg"


def commons_url(card: Card) -> str:
    """Прямая ссылка на файл: Викисклад раскладывает файлы по md5 их имени."""
    name = commons_filename(card)
    digest = hashlib.md5(name.encode()).hexdigest()  # noqa: S324 - не криптография, а путь
    return f"{COMMONS_BASE}/{digest[0]}/{digest[:2]}/{name}"


def local_path(card: Card) -> Path | None:
    """Своя картинка для карты, если её положили рядом."""
    for suffix in LOCAL_SUFFIXES:
        candidate = LOCAL_DIR / f"{card.id}{suffix}"
        if candidate.exists():
            return candidate
    return None


REVERSED_SUFFIX = ":rev"
DOWNLOAD_TIMEOUT = 15.0


def cache_key(card: Card, reversed_: bool) -> str:
    """Перевёрнутая картинка — другой файл, значит и ключ кэша другой."""
    return f"{card.id}{REVERSED_SUFFIX}" if reversed_ else card.id


def rotate_180(data: bytes) -> bytes:
    """Переворачивает картинку. Возвращает JPEG — Telegram принимает его везде."""
    with Image.open(BytesIO(data)) as image:
        turned = image.convert("RGB").transpose(Image.Transpose.ROTATE_180)
        buffer = BytesIO()
        turned.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


async def card_image_bytes(card: Card) -> bytes | None:
    """Файл карты: свой, если есть, иначе скачанный с Викисклада.

    Нужен только для поворота — в обычном случае картинку по ссылке забирает сам
    Telegram. Любая неудача здесь не повод ломать расклад, поэтому None.
    """
    if (path := local_path(card)) is not None:
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            logger.warning("Не прочитать %s: %s", path, exc)
            return None

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            response = await client.get(commons_url(card))
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        logger.warning("Не скачать картинку %s: %s", card.id, exc)
        return None


async def reversed_image_bytes(card: Card) -> bytes | None:
    """Готовая перевёрнутая картинка или None, если что-то не получилось."""
    data = await card_image_bytes(card)
    if data is None:
        return None
    try:
        return await asyncio.to_thread(rotate_180, data)
    except OSError as exc:  # битый или неожиданный формат
        logger.warning("Не повернуть картинку %s: %s", card.id, exc)
        return None
