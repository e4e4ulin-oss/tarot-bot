"""Лист расклада: пять и десять карт одной картинкой.

Отдельные фотографии хороши, пока их две-три. Дальше чат перестаёт быть
раскладом: порядок теряется, целого не видно. Здесь проверяется, что лист
собирается, что номера не разъезжаются и что недостающая картинка не ломает
ни лист, ни расклад.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from bot.tarot.layout import CARD_WIDTH, GAP, PAD, columns_for, compose, row_sizes, sheet


def card_bytes(colour: tuple[int, int, int] = (120, 20, 20)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (720, 1080), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_the_grid_stays_square_rather_than_long():
    """Полоса из пяти карт на телефоне — пять марок. Квадрат читается."""
    assert [columns_for(n) for n in range(1, 11)] == [1, 2, 3, 2, 3, 3, 3, 3, 3, 4]


def test_a_five_card_spread_fits_three_and_two():
    picture = Image.open(BytesIO(compose([card_bytes() for _ in range(5)])))

    assert picture.width == PAD * 2 + 3 * CARD_WIDTH + GAP * 2
    assert picture.height == PAD * 2 + 2 * round(CARD_WIDTH * 1.5) + GAP


def test_the_last_row_is_centred():
    """Неполный ряд слева выглядит обрезанным листом, а не раскладом."""
    picture = Image.open(BytesIO(compose([card_bytes((200, 30, 30)) for _ in range(5)])))
    width, height = picture.size
    bottom = round(height - CARD_WIDTH * 1.5 / 2)  # середина нижнего ряда

    # Поля слева и справа от нижнего ряда одинаковы: ищем первый и последний
    # столбец, где стоит карта, а не фон
    columns = [x for x in range(width) if picture.getpixel((x, bottom))[0] > 100]
    assert columns, "нижний ряд пустой"
    assert abs(columns[0] - (width - 1 - columns[-1])) <= 2


def test_a_missing_picture_keeps_the_numbering():
    """Карта без картинки оставляет своё место — иначе номера съедут."""
    full = compose([card_bytes() for _ in range(3)])
    gapped = compose([card_bytes(), None, card_bytes()])

    assert Image.open(BytesIO(full)).size == Image.open(BytesIO(gapped)).size


@pytest.mark.asyncio
async def test_no_pictures_at_all_means_no_sheet(monkeypatch):
    """Источник недоступен — расклад уходит текстом, а не падает."""
    monkeypatch.setattr("bot.tarot.layout.card_image_bytes", lambda card: _none())
    monkeypatch.setattr("bot.tarot.layout.reversed_image_bytes", lambda card: _none())

    from bot.tarot import draw_spread, get_spread

    drawn = draw_spread(get_spread("love"))
    assert len(drawn) == 5
    assert await sheet(drawn) is None


async def _none() -> None:
    return None


def test_rows_are_divided_evenly():
    """Остаток, собранный в последнем ряду, читается как недостача."""
    assert row_sizes(10) == [4, 3, 3]
    assert row_sizes(7) == [3, 2, 2]
    assert row_sizes(5) == [3, 2]
    assert all(sum(row_sizes(n)) == n for n in range(1, 11))
