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

from PIL import Image, ImageChops, ImageFilter, ImageStat

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


def shade(painted: Image.Image, back: Image.Image, deepen: float = 1.0) -> Image.Image:
    """Переносит на поле затемнения старой рубашки — прежде всего поле картуша.

    Картуш не золотой, а наоборот: ровное поле темнее фона. Маска по яркости его
    не видит, и на первой сборке он исчез у Жезлов целиком — у остальных гамм его
    выдавал только яркий контур. Поэтому поле умножается на отношение старой
    яркости к медиане старого фона: там, где рубашка была темнее фона, темнеет и
    новое поле, а на ровном фоне отношение около единицы и ничего не меняется.
    """
    grey = back.convert("L")
    level = median(grey)
    ratio = grey.point(
        [min(255, round(255 * min(1.0, v / level) ** deepen)) for v in range(256)]
    )

    return ImageChops.multiply(painted, Image.merge("RGB", [ratio] * 3))


def restore_level(result: Image.Image, target: tuple[int, int, int]) -> Image.Image:
    """Возвращает фону заданный тон после усиления затемнений.

    Усиление, которым вытягивают тёмный орнамент на светлой рубашке, затемняет
    заодно и всё поле: бронза Шута уехала от своих лиц на 15 при допуске 12.
    Здесь медиана результата приводится обратно к медиане заданного тона.
    """
    grey = result.convert("L")
    want = round(0.299 * target[0] + 0.587 * target[1] + 0.114 * target[2])
    scale = want / median(grey)
    if abs(scale - 1) < 0.01:
        return result
    lut = [min(255, round(v * scale)) for v in range(256)]
    return Image.merge("RGB", [ch.point(lut) for ch in result.split()])


def main() -> int:
    parser = argparse.ArgumentParser(description="Орнамент старой рубашки на новый фон")
    parser.add_argument("back", type=Path, help="старая рубашка — источник орнамента")
    parser.add_argument("field", type=Path, help="пустое живописное поле")
    parser.add_argument("tone", help='тон группы, например "#29232B"')
    parser.add_argument("out", type=Path)
    parser.add_argument("--dark", action="store_true",
                        help="орнамент темнее фона, как на бронзовой рубашке Шута")
    args = parser.parse_args()

    back = Image.open(args.back).convert("RGB")
    field = Image.open(args.field).convert("RGB").resize(back.size, Image.LANCZOS)

    # На светлой рубашке тёмный орнамент отличается от фона всего на проценты:
    # контур картуша у Шута темнее его бронзы на девять, и на фактурном поле он
    # тонет. Поэтому там затемнения усиливаются.
    painted = shade(tint(field, hex_to_rgb(args.tone)), back, 3.0 if args.dark else 1.0)

    if args.dark:
        # Весь орнамент здесь темнее фона и уже перенесён светотенью: золота нет
        result = painted
        told = "орнамент перенесён целиком как затемнение"
    else:
        mask, share = gold_mask(back)
        if not GOLD_MIN <= share <= GOLD_MAX:
            print(
                f"орнамент занял {share:.0%} кадра — порогом по яркости он от фона не "
                "отделяется. Если он темнее фона, пустите с --dark."
            )
            return 1
        result = Image.composite(back, painted, mask)
        told = f"орнамент ({share:.0%} кадра) пересажен"

    if args.dark:
        result = restore_level(result, hex_to_rgb(args.tone))
    result.save(args.out, quality=92, optimize=True)

    print(f"{told} на новое поле в тоне {args.tone} → {args.out}")
    print("симметрию при повороте добавьте через mirror_back.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
