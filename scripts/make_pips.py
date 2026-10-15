"""Собирает числовые карты масти из одного предмета, нарисованного кодом.

Рисовать пятьдесят шесть младших карт по одной — это пятьдесят шесть заходов и
пятьдесят шесть разных почерков. Предмет масти у них один и тот же, меняется
только количество, поэтому числовые карты собираются здесь: фон масти, латунный
предмет, раскладка по числу.

    python scripts/make_pips.py wands            # двойка … десятка
    python scripts/make_pips.py wands --only 5   # одна карта

Тузы и фигурные карты сюда не входят: там сюжет, а не счёт.
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "docs" / "design" / "cards"
BOARD = ROOT.parent / "miniapp" / "cards"

SIZE = (1024, 1536)
BOARD_SIZE = (720, 1080)

# Тона мастей — те же, что в docs/design/cards.md
SUITS: dict[str, str] = {
    "wands": "#7A2A08",
    "cups": "#123A4A",
    "swords": "#2E2A38",
    "pentacles": "#2C3A1E",
}

BRASS = (0xB9, 0x8A, 0x3C)

# Веер держится ниже поля под надпись: вершина вертикального жезла на 32% высоты
PIVOT = (0.50, 0.92)
LENGTH = 0.56


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def ground(tone: tuple[int, int, int]) -> Image.Image:
    """Фон масти: ровный тон, зерно холста и виньетка к углам."""
    width, height = SIZE
    base = Image.new("RGB", SIZE, tone)

    rng = random.Random(sum(tone))
    small = Image.new("L", (width // 6, height // 6))
    small.putdata([rng.randint(104, 150) for _ in range(small.width * small.height)])
    grain = small.resize(SIZE, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
    base = Image.blend(base, Image.merge("RGB", (grain, grain, grain)), 0.05)

    # Виньетка строится мелкой и растягивается: она и так мягкая
    mask = Image.new("L", (128, 192))
    pixels = []
    for y in range(mask.height):
        for x in range(mask.width):
            dx = (x / mask.width - 0.5) * 2
            dy = (y / mask.height - 0.5) * 2
            far = min(1.0, math.hypot(dx, dy) / 1.25)
            pixels.append(round(255 * (1 - 0.22 * far**2)))
    mask.putdata(pixels)
    shade = mask.resize(SIZE, Image.BICUBIC)

    out = Image.new("RGB", SIZE)
    out.putdata(
        [
            tuple(round(c * v / 255) for c in pixel)
            for pixel, v in zip(base.getdata(), shade.getdata(), strict=True)
        ]
    )
    return out


def shade_at(across: float) -> float:
    """Цилиндр под боковым светом: блик слева от центра, спад к краям."""
    return max(0.42, min(1.18, 1.18 - 2.6 * abs(across - 0.36) ** 1.5))


def wand(length: int, width: int) -> Image.Image:
    """Латунный жезл: сужается кверху, навершие-шар, кольцо у основания."""
    pad = width * 2
    im = Image.new("RGBA", (width * 2 + pad, length + pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    mid = im.width / 2

    for row in range(length):
        part = row / length  # 0 — вершина, 1 — основание
        half = width * (0.78 + 0.22 * part) / 2
        y = pad // 2 + row
        for step in range(int(half * 2) + 1):
            x = mid - half + step
            value = shade_at(step / max(1, half * 2))
            draw.point((x, y), tuple(min(255, round(c * value)) for c in BRASS) + (255,))

    knob = width * 0.92
    top = pad // 2
    for step in range(int(knob * 2) + 1):
        value = shade_at(step / (knob * 2))
        colour = tuple(min(255, round(c * value)) for c in BRASS) + (255,)
        x = mid - knob + step
        drop = knob * math.sqrt(max(0.0, 1 - ((x - mid) / knob) ** 2))
        draw.line([(x, top - drop), (x, top + drop)], fill=colour)

    return im


def angles(count: int) -> list[float]:
    """Веер: чем больше жезлов, тем шире разброс и тем он тяжелее."""
    spread = min(12 + 5.0 * count, 56)
    if count == 1:
        return [0.0]
    step = spread / (count - 1)
    return [-spread / 2 + step * i for i in range(count)]


def compose(count: int, tone: tuple[int, int, int]) -> Image.Image:
    width, height = SIZE
    card = ground(tone).convert("RGBA")

    pivot = (width * PIVOT[0], height * PIVOT[1])
    length = int(height * LENGTH)
    thickness = max(26, int(width * 0.050 - count * 1.4))

    shadow = Image.new("L", SIZE, 0)
    ImageDraw.Draw(shadow).ellipse(
        [pivot[0] - width * 0.26, pivot[1] - height * 0.015,
         pivot[0] + width * 0.26, pivot[1] + height * 0.035],
        fill=70,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(45))
    card = Image.composite(Image.new("RGBA", SIZE, (0, 0, 0, 255)), card, shadow)

    for index, angle in enumerate(angles(count)):
        # Крайние жезлы чуть короче: веер читается дугой, а не гребёнкой
        reach = 1.0 - 0.10 * abs(angle) / max(1.0, max(angles(count)))
        piece = wand(int(length * reach), thickness)
        box = max(piece.width, piece.height) * 2
        field = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        field.paste(piece, (box // 2 - piece.width // 2, box // 2 - piece.height), piece)
        turned = field.rotate(-angle, resample=Image.BICUBIC, center=(box // 2, box // 2))
        card.alpha_composite(turned, (round(pivot[0] - box // 2), round(pivot[1] - box // 2)))

    return card.convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser(description="Числовые карты масти")
    parser.add_argument("suit", choices=sorted(SUITS))
    parser.add_argument("--only", type=int, help="одно число вместо 2–10")
    args = parser.parse_args()

    tone = hex_to_rgb(SUITS[args.suit])
    numbers = [args.only] if args.only else list(range(2, 11))

    CARDS.mkdir(parents=True, exist_ok=True)
    BOARD.mkdir(parents=True, exist_ok=True)
    for number in numbers:
        card = compose(number, tone)
        name = f"{args.suit}_{number:02d}"
        card.save(CARDS / f"{name}.png")
        card.resize(BOARD_SIZE, Image.LANCZOS).save(
            BOARD / f"{name}.jpg", quality=86, optimize=True
        )
        print(f"{name}: {number} жезлов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
