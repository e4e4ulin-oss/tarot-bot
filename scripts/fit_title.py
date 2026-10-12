"""Освобождает поле под надпись, опуская кадр и дотягивая фон сверху.

Модель регулярно поднимает фигуру слишком высоко, и подпись ложится на волосы.
Перерисовывать из-за этого карту не нужно: фон над фигурой плоский, его можно
растянуть, а низ — обрезать, там всё равно уходящая за край ткань.

    python scripts/fit_title.py КАРТА.png [РЕЗУЛЬТАТ.png] [--start 0.24]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageStat

DEFAULT_START = 0.24
# Насколько пиксель должен отличаться от фона, чтобы считаться фигурой
FIGURE_THRESHOLD = 45


def figure_starts_at(image: Image.Image) -> float:
    width, height = image.size
    ground = ImageStat.Stat(image.crop((0, 0, width // 6, height // 6))).mean
    for y in range(0, height, 4):
        row = image.crop((0, y, width, y + 4)).resize((80, 1))
        far = max(
            sum(abs(p[i] - ground[i]) for i in range(3)) / 3 for p in row.getdata()
        )
        if far > FIGURE_THRESHOLD:
            return y / height
    return 1.0


def lower(image: Image.Image, start: float = DEFAULT_START) -> Image.Image:
    """Опускает фигуру так, чтобы она начиналась не выше заданной доли высоты."""
    width, height = image.size
    current = figure_starts_at(image)
    if current >= start:
        return image

    cut = max(1, int(current * height) - 2)
    shift = int((start - current) * height)

    # Пустой фон сверху растягивается: он плоский, и растяжение незаметно,
    # а виньетка сохраняет направление
    band = image.crop((0, 0, width, cut)).resize((width, cut + shift), Image.LANCZOS)
    out = Image.new("RGB", (width, height))
    out.paste(band, (0, 0))
    out.paste(image.crop((0, cut, width, height - shift)), (0, cut + shift))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Опустить фигуру под поле надписи")
    parser.add_argument("card", type=Path)
    parser.add_argument("out", type=Path, nargs="?")
    parser.add_argument("--start", type=float, default=DEFAULT_START)
    args = parser.parse_args()

    image = Image.open(args.card).convert("RGB")
    before = figure_starts_at(image)
    fixed = lower(image, args.start)
    after = figure_starts_at(fixed)

    if after <= before:
        print(f"фигура и так начинается на {before:.0%} — ничего не меняю")
        return 0

    fixed.save(args.out or args.card)
    print(f"фигура опущена с {before:.0%} до {after:.0%} высоты → {args.out or args.card}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
