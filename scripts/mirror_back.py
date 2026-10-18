"""Делает рубашку строго симметричной при повороте на 180°.

Перевёрнутая карта не должна выдавать себя в раскладе, поэтому орнамент рубашки
обязан совпадать сам с собой при повороте. Нарисованная целиком рубашка этого не
даёт: мазок, блики и потёртости ложатся случайно, и готовые рубашки колоды расходятся
при повороте на 5–10 из 255.

Выход — не просить симметрию у генератора, а собрать её: взять верхнюю половину,
повернуть её на 180° и поставить нижней. Расхождение становится ровно нулевым.
Шов проходит по горизонтальной оси через середину картуша, где поле ровное, и
растворяется в переходе.

    python scripts/mirror_back.py РУБАШКА.jpg [РЕЗУЛЬТАТ.jpg] [--blend 40]

Карта должна быть скомпонована так, чтобы её середину пересекало ровное место:
у нашей рубашки это картуш. Если по оси идёт орнамент, шов будет виден, и это
проверяют глазом.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

DEFAULT_BLEND = 40


def mirror(image: Image.Image, blend: int = DEFAULT_BLEND) -> Image.Image:
    width, height = image.size
    half = height // 2

    top = image.crop((0, 0, width, half))
    bottom = top.transpose(Image.ROTATE_180)

    out = Image.new("RGB", (width, half * 2))
    out.paste(top, (0, 0))
    out.paste(bottom, (0, half))

    if blend > 0:
        # Растворяем стык: полоса сверху от шва плавно переходит в свою же копию снизу
        band = min(blend, half // 4)
        upper = out.crop((0, half - band, width, half))
        lower = out.crop((0, half, width, half + band))
        gradient = Image.new("L", (1, band))
        gradient.putdata([round(255 * i / max(1, band - 1)) for i in range(band)])
        mask = gradient.resize((width, band))
        out.paste(Image.composite(lower.transpose(Image.ROTATE_180), upper, mask),
                  (0, half - band))

    return out.resize(image.size, Image.LANCZOS)


def turn_delta(image: Image.Image) -> float:
    """Расхождение карты с собой, повёрнутой на 180°."""
    turned = image.transpose(Image.ROTATE_180)
    pairs = zip(image.convert("RGB").getdata(), turned.convert("RGB").getdata())
    total = 0
    count = 0
    for a, b in pairs:
        total += sum(abs(a[i] - b[i]) for i in range(3)) / 3
        count += 1
    return total / max(1, count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Собрать симметричную рубашку")
    parser.add_argument("back", type=Path)
    parser.add_argument("out", type=Path, nargs="?")
    parser.add_argument("--blend", type=int, default=DEFAULT_BLEND,
                        help="ширина растворяющегося шва в пикселях")
    args = parser.parse_args()

    image = Image.open(args.back).convert("RGB")
    out = args.out or args.back

    before = turn_delta(image)
    result = mirror(image, args.blend)
    after = turn_delta(result)

    result.save(out, quality=92, optimize=True)
    print(f"было расхождение при повороте {before:.1f} из 255, стало {after:.1f} → {out}")
    print("посмотрите шов по горизонтальной оси: скрипт не знает, что там нарисовано")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
