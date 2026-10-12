"""Собирает рубашки всех гамм из слоёв одной нарисованной.

Рубашка обязана совпадать по фону с лицом карты, а гамм в колоде восемь. Рисовать
восемь рубашек заново значит восемь раз ловить одинаковый орнамент; вместо этого
перекрашивается основа, а золото остаётся золотом.

    python scripts/make_backs.py ПАПКА_СО_СЛОЯМИ ПАПКА_РЕЗУЛЬТАТА

Слои ожидаются такими, какими их отдаёт генератор: 01_frame_rosettes.png,
02_rays_stars.png, 03_cartouche.png, 04_red_canvas.png.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# Те же тона, что у лицевых сторон: docs/design/cards.md
TONES: dict[str, tuple[int, int, int]] = {
    "fool": (0xC9, 0xB6, 0x9A),
    "major1": (0x8F, 0x03, 0x03),
    "major2": (0x10, 0x1A, 0x33),
    "major3": (0x15, 0x12, 0x0F),
    "wands": (0x7A, 0x2A, 0x08),
    "cups": (0x12, 0x3A, 0x4A),
    "swords": (0x2E, 0x2A, 0x38),
    "pentacles": (0x2C, 0x3A, 0x1E),
}

LAYER_FRAME = "01_frame_rosettes.png"
LAYER_RAYS = "02_rays_stars.png"
LAYER_CARTOUCHE = "03_cartouche.png"
LAYER_CANVAS = "04_red_canvas.png"

# Насколько поле картуша темнее фона: на исходной рубашке разница небольшая
CARTOUCHE_DARKEN = 0.86

# Орнамент перекрашивается в чистое золото. Иначе на светлом и холодном фоне
# вылезает красная подложка, оставшаяся в полупрозрачных краях слоёв.
GOLD = (0xD9, 0xAC, 0x5E)


def ornament_strength(g: int) -> float:
    """Насколько пиксель принадлежит орнаменту, а не фону под ним.

    Слои приходят с куском красной подложки вокруг линий, и она непрозрачная —
    просто выкинуть её по альфе нельзя. Зато зелёный канал разводит их начисто:
    у красного фона он почти нулевой, у золота высокий. Промежуточные значения
    дают мягкий край вместо зубцов.
    """
    return min(1.0, max(0.0, (g - 20) / 90))


def tinted_canvas(canvas: Image.Image, tone: tuple[int, int, int]) -> Image.Image:
    """Тот же холст, но в другом цвете: фактура берётся из яркости исходника."""
    grey = canvas.convert("L")
    mean = sum(grey.getdata()) / (grey.width * grey.height)
    out = Image.new("RGB", canvas.size)
    out.putdata(
        [
            tuple(min(255, max(0, round(c * value / mean))) for c in tone)
            for value in grey.getdata()
        ]
    )
    return out


def golden(layer: Image.Image) -> Image.Image:
    """Орнамент в чистом золоте, подложка выброшена."""
    source = layer.convert("RGBA")
    grey = source.convert("L")
    values = [v for v, (_, _, _, a) in zip(grey.getdata(), source.getdata(), strict=True) if a > 0]
    mean = max(1.0, sum(values) / max(1, len(values)))

    pixels = []
    for (_, g, _, a), value in zip(source.getdata(), grey.getdata(), strict=True):
        strength = ornament_strength(g) if a > 0 else 0.0
        if strength == 0.0:
            pixels.append((0, 0, 0, 0))
            continue
        shade = min(1.15, 0.75 + 0.5 * (value / mean))
        pixels.append((*(min(255, round(c * shade)) for c in GOLD), round(a * strength)))

    out = Image.new("RGBA", source.size)
    out.putdata(pixels)
    return out


def tinted_cartouche(cartouche: Image.Image, tone: tuple[int, int, int]) -> Image.Image:
    """Заливка картуша в цвете группы, контур — золотой."""
    source = cartouche.convert("RGBA")
    fill = tuple(round(c * CARTOUCHE_DARKEN) for c in tone)

    pixels = []
    for _r, g, _b, a in source.getdata():
        if a == 0:
            pixels.append((0, 0, 0, 0))
            continue
        strength = ornament_strength(g)
        colour = tuple(
            round(fill[i] * (1 - strength) + GOLD[i] * strength) for i in range(3)
        )
        pixels.append((*colour, a))

    out = Image.new("RGBA", source.size)
    out.putdata(pixels)
    return out


def build(layers: Path, tone: tuple[int, int, int]) -> Image.Image:
    canvas = Image.open(layers / LAYER_CANVAS).convert("RGB")
    back = tinted_canvas(canvas, tone).convert("RGBA")
    back.alpha_composite(golden(Image.open(layers / LAYER_RAYS)))
    back.alpha_composite(tinted_cartouche(Image.open(layers / LAYER_CARTOUCHE), tone))
    back.alpha_composite(golden(Image.open(layers / LAYER_FRAME)))
    return back.convert("RGB")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    layers, out_dir = Path(argv[1]), Path(argv[2])
    missing = [n for n in (LAYER_FRAME, LAYER_RAYS, LAYER_CARTOUCHE, LAYER_CANVAS)
               if not (layers / n).exists()]
    if missing:
        print("не хватает слоёв:", ", ".join(missing))
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    for key, tone in TONES.items():
        back = build(layers, tone)
        target = out_dir / f"back-{key}.jpg"
        back.resize((720, 1080), Image.LANCZOS).save(target, quality=86, optimize=True)
        print(f"{target.name}: фон #{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
