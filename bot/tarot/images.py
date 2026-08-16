"""Изображения карт.

Источник по умолчанию — сканы колоды Райдера-Уэйта с Викисклада (рисунки Памелы
Колман Смит, 1909; общественное достояние). Файлы не хранятся в репозитории:
Telegram скачивает картинку по ссылке сам, а полученный file_id бот кэширует,
поэтому к Викискладу он обращается один раз на карту.

Если рядом лежит своя картинка — `bot/tarot/data/cards/<id>.jpg` — используется она.
Так Анастасия может постепенно заменить сканы фотографиями собственной колоды,
не трогая код: имя файла совпадает с id карты (major_00, cups_01, wands_14…).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .deck import Card

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
