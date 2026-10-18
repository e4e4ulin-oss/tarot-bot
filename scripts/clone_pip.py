"""Достраивает ряд однотипных предметов, клонируя один из них со смещением.

Модель уверенно раскладывает три, четыре, пять предметов и почти всегда недобирает
один-два, когда их девять или десять. Перерисовка карты это не лечит: девятка Мечей
пришла с восемью мечами трижды подряд, в последний раз — безупречной ровной лесенкой.

Если предметы лежат регулярно и фон вокруг ровный, девятый предмет не надо рисовать:
он уже нарисован восемь раз. Скрипт вырезает один вместе с куском фона и вклеивает
копию со смещением, растворяя края заплатки по фону.

    python scripts/clone_pip.py КАРТА.png --from 605,775,965,835 --shift 55,56 [РЕЗУЛЬТАТ.png]

`--from` — рамка вокруг образца с запасом фона, `--shift` — куда положить копию.
Шаг берите из самого ряда: соседние предметы задают его точно.

Это работает только на ровном фоне и только в регулярном ряду. Скрипт проверяет, что
на месте копии нет чужой живописи, и отказывается, если она там есть; но совпадение
шага и правдоподобие ряда он проверить не может, поэтому результат обязательно
смотрят глазами.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

# Насколько место под копию может отличаться от фона, чтобы считаться пустым
BUSY_THRESHOLD = 70
# Ширина растворяющейся каймы по краю заплатки
FEATHER = 14


def pair(value: str, count: int, name: str) -> tuple[int, ...]:
    parts = tuple(int(v) for v in value.split(","))
    if len(parts) != count:
        raise SystemExit(f"{name} ожидает {count} числа через запятую")
    return parts


def ground(image: Image.Image) -> tuple[float, float, float]:
    corner = image.crop((0, 0, image.width // 6, image.height // 6))
    return tuple(ImageStat.Stat(corner).mean[:3])  # type: ignore[return-value]


def busiest(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Насколько сильно место под копию отличается от фона в худшей точке."""
    base = ground(image)
    zone = image.crop(box).resize((60, 60))
    return max(
        sum(abs(p[i] - base[i]) for i in range(3)) / 3 for p in zone.getdata()
    )


def clone(
    image: Image.Image, box: tuple[int, int, int, int], shift: tuple[int, int]
) -> Image.Image:
    patch = image.crop(box)
    mask = Image.new("L", patch.size, 0)
    mask.paste(
        255, (FEATHER, FEATHER, patch.width - FEATHER, patch.height - FEATHER)
    )
    mask = mask.filter(ImageFilter.GaussianBlur(FEATHER // 2))

    out = image.copy()
    out.paste(patch, (box[0] + shift[0], box[1] + shift[1]), mask)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Достроить ряд предметов копией")
    parser.add_argument("card", type=Path)
    parser.add_argument("out", type=Path, nargs="?")
    parser.add_argument("--from", dest="box", required=True, help="левый,верх,правый,низ")
    parser.add_argument("--shift", required=True, help="сдвиг по x,y")
    parser.add_argument("--force", action="store_true", help="вклеить поверх живописи")
    args = parser.parse_args()

    image = Image.open(args.card).convert("RGB")
    box = pair(args.box, 4, "--from")
    shift = pair(args.shift, 2, "--shift")
    out = args.out or args.card

    width, height = box[2] - box[0], box[3] - box[1]
    dest = (box[0] + shift[0], box[1] + shift[1])
    if not (0 <= dest[0] and 0 <= dest[1]
            and dest[0] + width <= image.width and dest[1] + height <= image.height):
        print("копия не помещается в кадр — уменьшите сдвиг или рамку")
        return 1

    busy = busiest(image, (dest[0], dest[1], dest[0] + width, dest[1] + height))
    if busy > BUSY_THRESHOLD and not args.force:
        print(
            f"на месте копии уже есть живопись (отличие от фона {busy:.0f}) — "
            "копия закроет её. Подвиньте сдвиг или пустите с --force."
        )
        return 1

    clone(image, box, shift).save(out)
    print(f"копия образца {box} вклеена со сдвигом {shift} → {out}")
    print("посмотрите результат глазами: шаг ряда скрипт не проверяет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
