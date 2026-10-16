"""Поднимает холодный фон до цвета группы, отделяя его от фигуры по теплу.

`fix_ground.py` ищет фон заливкой от краёв по близости к затравке. На холодных
мастях это не работает: фон там выходит настолько тёмным, что тёмные волосы,
складки бархата и тени попадают в допуск, заливка уходит внутрь фигуры и красит
её синим. Доля кадра при этом остаётся в норме, поэтому предохранитель на 75%
молчит — брак виден только глазом.

Здесь фон отделяется не по близости к затравке, а по цветовому семейству. На
Кубках и Мечах фон холодный, а фигура целиком тёплая: кожа, рыжие волосы, бордо,
латунь. Разница синего и красного каналов разводит их начисто — на первом листе
Кубков фон дал +28, фигура от -31 до -56.

    python scripts/cool_ground.py КАРТА.png "#123A4A" [РЕЗУЛЬТАТ.png]

Для тёплых групп (кармин, бронза Шута, Жезлы) скрипт не годится: там фон и фигура
одного семейства, и он сам отказывается.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

# Ниже этой разницы синего и красного пиксель ещё не фон, выше — уже фон.
# Между ними маска идёт плавно, иначе по краю волос встаёт ступенька.
COLD_LOW = 2
COLD_HIGH = 18

# Насколько холоден должен быть угол карты, чтобы масть вообще считалась холодной
COLD_ENOUGH = 10


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def coldness(image: Image.Image) -> Image.Image:
    """Насколько пиксель холоднее нейтрали: синий канал минус красный."""
    red, _, blue = image.split()
    return ImageChops.subtract(blue, red)


def ground_mask(image: Image.Image) -> Image.Image:
    """Маска фона: плавный переход от COLD_LOW к COLD_HIGH по холодности."""
    span = max(1, COLD_HIGH - COLD_LOW)
    ramp = [min(255, max(0, round((v - COLD_LOW) * 255 / span))) for v in range(256)]
    mask = coldness(image).point(ramp)
    return mask.filter(ImageFilter.GaussianBlur(1.4))


def recolour(image: Image.Image, target: tuple[int, int, int]) -> Image.Image:
    """Фон в заданный цвет с сохранением фактуры и виньетки."""
    mask = ground_mask(image)
    grey = image.convert("L")

    mean = max(1.0, ImageStat.Stat(grey, mask).mean[0])
    painted = Image.merge(
        "RGB",
        [
            grey.point([min(255, round(c * v / mean)) for v in range(256)])
            for c in target
        ],
    )
    return Image.composite(painted, image, mask)


def main() -> int:
    parser = argparse.ArgumentParser(description="Поднять холодный фон до тона группы")
    parser.add_argument("card", type=Path)
    parser.add_argument("tone", help='цвет группы, например "#123A4A"')
    parser.add_argument("out", type=Path, nargs="?")
    args = parser.parse_args()

    image = Image.open(args.card).convert("RGB")
    out = args.out or args.card

    corner = ImageStat.Stat(
        coldness(image).crop((0, 0, image.width // 6, image.height // 6))
    ).mean[0]
    if corner < COLD_ENOUGH:
        print(
            f"угол карты холоднее нейтрали всего на {corner:.0f} — фон здесь не холодный. "
            "Для тёплых групп берите fix_ground.py."
        )
        return 1

    mask = ground_mask(image)
    share = ImageStat.Stat(mask).mean[0] / 255
    if share < 0.15:
        print(f"фон занял всего {share:.0%} кадра — проверьте карту")
        return 1
    if share > 0.75:
        print(
            f"маска захватила {share:.0%} кадра — на фигуре есть холодная ткань, "
            "она покрасится вместе с фоном. Такую карту надо перерисовать."
        )
        return 1

    recolour(image, hex_to_rgb(args.tone)).save(out)
    print(f"фон ({share:.0%} кадра) поднят до {args.tone} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
