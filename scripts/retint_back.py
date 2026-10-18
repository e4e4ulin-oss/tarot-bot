"""Перекрашивает готовую рубашку в тон своей группы, не трогая золото.

`make_backs.py` собирает рубашку из слоёв и красит её правильно, но слои есть не
всегда, а тона групп уточняются по уже нарисованным картам. Рубашки от этого
отстают: шесть из восьми разошлись со своими лицами на 13–20 при допуске 12, потому
что собраны вокруг заказанных тонов, а лица во всех группах вышли темнее заказа.

Орнамент на рубашке — тонкие золотые линии, звёзды и лучи: он занимает около шести
процентов кадра и заметно ярче фона. Этого хватает, чтобы отделить его порогом по
яркости и перекрасить всё остальное, сохранив фактуру и виньетку.

    python scripts/retint_back.py РУБАШКА.jpg "#29232B" [РЕЗУЛЬТАТ.jpg]

Порог берётся от медианы яркости; если орнамент и фон по светлоте близки, скрипт
отказывается. Так и должно быть на бронзовой рубашке Шута: золото на светлой бронзе
порогом не отделяется, и такую рубашку собирают только из слоёв.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

# Во сколько раз орнамент ярче медианы фона: порог отделения
GOLD_ABOVE = 1.35
# Допустимая доля орнамента в кадре
GOLD_MIN, GOLD_MAX = 0.02, 0.20


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def gold_mask(grey: Image.Image, cut: float) -> Image.Image:
    """Маска орнамента: мягкий порог по яркости, чтобы не рвать тонкие линии."""
    span = max(1.0, cut * 0.25)
    ramp = [
        min(255, max(0, round((v - cut + span) * 255 / (2 * span)))) for v in range(256)
    ]
    return grey.point(ramp).filter(ImageFilter.GaussianBlur(0.6))


def median(grey: Image.Image, mask: Image.Image | None = None) -> float:
    counts = grey.histogram(mask) if mask else grey.histogram()
    half = sum(counts) / 2
    seen = 0
    for value, count in enumerate(counts):
        seen += count
        if seen >= half:
            return max(1.0, float(value))
    return 1.0


def retint(image: Image.Image, target: tuple[int, int, int]) -> tuple[Image.Image, float]:
    grey = image.convert("L")
    cut = median(grey) * GOLD_ABOVE
    gold = gold_mask(grey, cut)
    share = ImageStat.Stat(gold).mean[0] / 255

    ground = gold.point(lambda v: 255 - v)
    level = median(grey, ground.point(lambda v: 255 if v > 200 else 0))
    painted = Image.merge(
        "RGB",
        [
            grey.point([min(255, round(c * v / level)) for v in range(256)])
            for c in target
        ],
    )
    # золото остаётся из оригинала, фон берётся перекрашенный
    return Image.composite(image, painted, gold), share


def main() -> int:
    parser = argparse.ArgumentParser(description="Перекрасить рубашку в тон группы")
    parser.add_argument("back", type=Path)
    parser.add_argument("tone", help='цвет группы, например "#29232B"')
    parser.add_argument("out", type=Path, nargs="?")
    args = parser.parse_args()

    image = Image.open(args.back).convert("RGB")
    out = args.out or args.back

    result, share = retint(image, hex_to_rgb(args.tone))
    if not GOLD_MIN <= share <= GOLD_MAX:
        print(
            f"орнамент занял {share:.0%} кадра — порогом по яркости он от фона не "
            "отделяется. Такую рубашку собирают из слоёв через make_backs.py."
        )
        return 1

    result.save(out, quality=92, optimize=True)
    tone = ImageStat.Stat(
        result.crop((0, 0, result.width // 6, result.height // 6))
    ).mean[:3]
    got = "#{:02X}{:02X}{:02X}".format(*(round(v) for v in tone))
    print(f"рубашка перекрашена в {args.tone} (орнамент {share:.0%}), угол стал {got} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
