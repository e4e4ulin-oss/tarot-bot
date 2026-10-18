"""Пересаживает орнамент готовой рубашки на новый живописный фон.

В рубашке плоским выглядит только фон: ровная заливка рядом с масляной живописью лиц
читается как векторная иконка. Орнамент при этом уже нарисован, принят и симметричен,
и просить генератор повторить его значит получить другой рисунок с другими ошибками.

Поэтому у генератора берут одно пустое живописное поле, а орнамент снимают со старой
рубашки порогом по яркости — он там вдвое ярче фона и занимает около шести процентов
кадра. Поле перекрашивается в тон нужной группы (орнамента на нём нет, маска не
нужна), и орнамент ложится сверху.

    python scripts/reface_back.py СТАРАЯ.jpg ПОЛЕ.png "#29232B" РЕЗУЛЬТАТ.jpg

Симметрию при повороте добирают отдельно, через mirror_back.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

# Во сколько раз орнамент ярче медианы фона
GOLD_ABOVE = 1.35
GOLD_MIN, GOLD_MAX = 0.02, 0.20


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def median(grey: Image.Image, mask: Image.Image | None = None) -> float:
    counts = grey.histogram(mask) if mask else grey.histogram()
    half = sum(counts) / 2
    seen = 0
    for value, count in enumerate(counts):
        seen += count
        if seen >= half:
            return max(1.0, float(value))
    return 1.0


def gold_mask(back: Image.Image) -> tuple[Image.Image, float]:
    """Маска орнамента старой рубашки и его доля в кадре."""
    grey = back.convert("L")
    cut = median(grey) * GOLD_ABOVE
    span = max(1.0, cut * 0.25)
    ramp = [
        min(255, max(0, round((v - cut + span) * 255 / (2 * span)))) for v in range(256)
    ]
    mask = grey.point(ramp).filter(ImageFilter.GaussianBlur(0.6))
    return mask, ImageStat.Stat(mask).mean[0] / 255


def tint(field: Image.Image, target: tuple[int, int, int]) -> Image.Image:
    """Красит пустое поле в заданный тон, сохраняя его светотень и фактуру."""
    grey = field.convert("L")
    level = median(grey)
    return Image.merge(
        "RGB",
        [
            grey.point([min(255, round(c * v / level)) for v in range(256)])
            for c in target
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Орнамент старой рубашки на новый фон")
    parser.add_argument("back", type=Path, help="старая рубашка — источник орнамента")
    parser.add_argument("field", type=Path, help="пустое живописное поле")
    parser.add_argument("tone", help='тон группы, например "#29232B"')
    parser.add_argument("out", type=Path)
    args = parser.parse_args()

    back = Image.open(args.back).convert("RGB")
    field = Image.open(args.field).convert("RGB").resize(back.size, Image.LANCZOS)

    mask, share = gold_mask(back)
    if not GOLD_MIN <= share <= GOLD_MAX:
        print(
            f"орнамент занял {share:.0%} кадра — порогом по яркости он от фона не "
            "отделяется, и снять его с этой рубашки нельзя."
        )
        return 1

    painted = tint(field, hex_to_rgb(args.tone))
    result = Image.composite(back, painted, mask)
    result.save(args.out, quality=92, optimize=True)

    print(f"орнамент ({share:.0%} кадра) пересажен на новое поле в тоне {args.tone} → {args.out}")
    print("симметрию при повороте добавьте через mirror_back.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
