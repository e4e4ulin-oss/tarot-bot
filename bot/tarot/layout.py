"""Расклад одной картинкой.

Телеграм показывает до десяти фотографий альбомом, но пять карт по
отдельности — это уже не расклад, а лента, в которой не видно ни порядка, ни
целого. Поэтому большие расклады собираются в один лист: карты в порядке
расклада, с номерами, которые совпадают с разбором.

Карта в лист вписывается целиком, без обрезки: у сканов Уэйта и у нарисованных
карт разные пропорции, и подгонять их кадром — значит рубить рисунок.
"""

from __future__ import annotations

import asyncio
import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .draw import DrawnCard
from .images import card_image_bytes, reversed_image_bytes

CARD_WIDTH = 460
CARD_RATIO = 1.5  # место под карту: высота к ширине
GAP = 16
PAD = 22

# Тёмный тёплый фон: он не спорит с грунтами карт и не светит в глаза ночью
BACKGROUND = (0x14, 0x12, 0x10)
EMPTY = (0x23, 0x20, 0x1C)  # место карты, картинку которой не достали

# Номер карты: латунный кружок, тёмная цифра. Латунь взята из палитры колоды
BRONZE = (0x9C, 0x7A, 0x43)
INK = (0x17, 0x17, 0x14)
BADGE = 34  # радиус кружка
NUMBER_SIZE = 34

JPEG_QUALITY = 86


def columns_for(count: int) -> int:
    """Сколько карт в ряду.

    Квадрат читается лучше полосы: три карты в ряд остаются крупными на
    телефоне, а десять в два ряда по пять превращаются в марки.
    """
    if count <= 3:
        return count
    if count == 4:
        return 2
    return min(4, math.ceil(math.sqrt(count)))


def row_sizes(count: int) -> list[int]:
    """По скольку карт в каждом ряду.

    Ряды делятся поровну: десять карт — это 4 + 3 + 3, а не 4 + 4 + 2.
    Остаток, собранный в последнем ряду, читается как недостача.
    """
    columns = columns_for(count)
    rows = math.ceil(count / columns)
    base, extra = divmod(count, rows)
    return [base + (1 if index < extra else 0) for index in range(rows)]


def _place(card: Image.Image, cell: tuple[int, int]) -> Image.Image:
    """Карта, вписанная в место целиком — пропорции чужими не делаем."""
    return ImageOps.contain(card.convert("RGB"), cell, Image.LANCZOS)


def compose(files: list[bytes | None]) -> bytes:
    """Лист с картами в порядке расклада."""
    count = len(files)
    sizes = row_sizes(count)
    cell = (CARD_WIDTH, round(CARD_WIDTH * CARD_RATIO))

    widest = max(sizes)
    width = PAD * 2 + widest * cell[0] + GAP * (widest - 1)
    height = PAD * 2 + len(sizes) * cell[1] + GAP * (len(sizes) - 1)
    sheet = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=NUMBER_SIZE)

    places = [
        (row, column) for row, size in enumerate(sizes) for column in range(size)
    ]

    for index, data in enumerate(files):
        row, column = places[index]
        # Неполный ряд ставится по центру: иначе лист заваливается влево и
        # выглядит обрезанным
        row_width = sizes[row] * cell[0] + GAP * (sizes[row] - 1)
        left = (width - row_width) // 2 + column * (cell[0] + GAP)
        top = PAD + row * (cell[1] + GAP)

        if data is None:
            draw.rectangle([left, top, left + cell[0], top + cell[1]], fill=EMPTY)
        else:
            with Image.open(BytesIO(data)) as card:
                fitted = _place(card, cell)
            sheet.paste(
                fitted,
                (left + (cell[0] - fitted.width) // 2, top + (cell[1] - fitted.height) // 2),
            )

        middle = (left + BADGE + 6, top + BADGE + 6)
        draw.ellipse(
            [middle[0] - BADGE, middle[1] - BADGE, middle[0] + BADGE, middle[1] + BADGE],
            fill=BRONZE,
        )
        draw.text(middle, str(index + 1), font=font, fill=INK, anchor="mm")

    buffer = BytesIO()
    sheet.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


async def sheet(drawn: list[DrawnCard]) -> bytes | None:
    """Готовый лист расклада или None, если ни одной картинки не досталось.

    Картинки — украшение: если источник недоступен, расклад уходит текстом,
    а не падает. Одна недостающая карта оставляет на листе пустое место, и
    номера остаются на своих местах.
    """
    files = [
        await (reversed_image_bytes(item.card) if item.reversed else card_image_bytes(item.card))
        for item in drawn
    ]
    if not any(data is not None for data in files):
        return None
    return await asyncio.to_thread(compose, files)
