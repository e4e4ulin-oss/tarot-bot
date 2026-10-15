"""Приближает или отдаляет карту, не перерисовывая её.

Сцена иногда выходит мелкой в кадре или, наоборот, упирается в края. Кадр при
этом исправим: фигура стоит на нижнем крае, поэтому и приближение, и отдаление
считаются от низа, а сверху добавляется или убирается пустой фон.

    python scripts/reframe.py wands_06 1.15      # приблизить
    python scripts/reframe.py wands_11 0.93      # отдалить

Карта пересохраняется только если после этого проходит приёмку: приближение
легко загоняет фигуру в поле под надпись.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_cards import inspect  # noqa: E402
from split_sheet import BOARD, BOARD_SIZE, CARDS, CARD_SIZE, tone_for  # noqa: E402


def zoom(card: Image.Image, factor: float) -> Image.Image:
    """Приближение — вырез от низа, отдаление — вклейка в размытый свой же фон."""
    width, height = card.size
    if factor > 1:
        window = (round(width / factor), round(height / factor))
        left = (width - window[0]) // 2
        box = (left, height - window[1], left + window[0], height)
        return card.crop(box).resize((width, height), Image.LANCZOS)

    # Поля вокруг дотягиваются не краем карты, а полосой чуть внутри него: сам
    # край уже затемнён виньеткой, и растянутый он даёт по периметру тёмную рамку.
    # Затемнение к обрезу возвращается отдельно, ровным спадом.
    inset = 24
    small = card.resize((round(width * factor), round(height * factor)), Image.LANCZOS)
    left = (width - small.width) // 2
    top = height - small.height

    field = Image.new("RGB", (width, height))
    field.paste(small, (left, top))
    if top:
        band = small.crop((0, inset, small.width, inset + 2)).resize((small.width, top))
        field.paste(band, (left, 0))
    if left:
        column = field.crop((left + inset, 0, left + inset + 1, height))
        field.paste(column.resize((left + inset, height)), (0, 0))
        far = left + small.width - inset
        column = field.crop((far - 1, 0, far, height))
        field.paste(column.resize((width - far, height)), (far, 0))

    return darken_edges(field, left, top)


def darken_edges(card: Image.Image, left: int, top: int) -> Image.Image:
    """Возвращает спад к обрезу, съеденный растяжением полей."""
    width, height = card.size
    if not left and not top:
        return card

    mask = Image.new("L", (64, 96), 255)
    pixels = []
    for y in range(mask.height):
        for x in range(mask.width):
            dx = min(x, mask.width - 1 - x) / mask.width
            dy = min(y, mask.height - 1 - y) / mask.height
            near = min(1.0, min(dx, dy) / 0.10)
            pixels.append(round(255 * (0.86 + 0.14 * near)))
    mask.putdata(pixels)
    shade = mask.resize((width, height), Image.BICUBIC)

    out = Image.new("RGB", (width, height))
    out.putdata(
        [
            tuple(round(c * v / 255) for c in pixel)
            for pixel, v in zip(card.getdata(), shade.getdata(), strict=True)
        ]
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Изменить крупность карты")
    parser.add_argument("card_id")
    parser.add_argument("factor", type=float, help="больше 1 — приблизить, меньше — отдалить")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source = CARDS / f"{args.card_id}.png"
    if not source.exists():
        raise SystemExit(f"нет такой карты: {source}")

    card = zoom(Image.open(source).convert("RGB"), args.factor)
    work = Path("/tmp") / f"{args.card_id}-reframe.png"
    card.save(work)
    check = inspect(work, tone_for(args.card_id))
    print(f"{args.card_id} ×{args.factor}: {check.report}")

    if not check.ok and not args.force:
        print("  не сохраняю — после такого кадра карта не проходит приёмку")
        return 1

    card = card.resize(CARD_SIZE, Image.LANCZOS)
    card.save(source)
    card.resize(BOARD_SIZE, Image.LANCZOS).save(
        BOARD / f"{args.card_id}.jpg", quality=86, optimize=True
    )
    print("  сохранено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
