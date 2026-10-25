"""Оформление бота: аватар и картинки из сгенерированного исходника.

У Telegram три места, куда кладётся картинка, и у каждого свой размер. Правка
размера руками — это каждый раз заново вспоминать, какой он, и каждый раз резать
кадр на глаз. Здесь размеры названы один раз, а обрезка объявляется вслух:
картинка сперва режется по центру до нужных пропорций, и скрипт говорит, сколько
пикселей ушло. Если уходит много — значит кадр не тот, и это видно до публикации.

    python scripts/make_brand.py avatar исходник.png
    python scripts/make_brand.py cover исходник.png
    python scripts/make_brand.py intro исходник.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "design" / "brand"

# Куда что идёт в Telegram и какого размера
SLOTS: dict[str, tuple[tuple[int, int], str]] = {
    "avatar": ((512, 512), "/setuserpic — аватар бота, Telegram режет его в круг"),
    "cover": ((640, 360), "Configure Mini App → Photo — обложка мини-приложения"),
    "intro": ((640, 360), "/setdescriptionpic — картинка в пустом чате до «Начать»"),
}

QUALITY = 92
# Больше этой доли кадра резать молча нельзя: значит исходник не тех пропорций
LOUD_CROP = 0.04


def fit(source: Image.Image, size: tuple[int, int]) -> tuple[Image.Image, int, int]:
    """Центральная обрезка до нужных пропорций и перевод в нужный размер."""
    want = size[0] / size[1]
    have = source.width / source.height

    if have > want:  # шире, чем нужно, — режем по бокам
        width = round(source.height * want)
        left = (source.width - width) // 2
        box = (left, 0, left + width, source.height)
    else:  # выше, чем нужно, — режем сверху и снизу
        height = round(source.width / want)
        top = (source.height - height) // 2
        box = (0, top, source.width, top + height)

    cropped = source.crop(box)
    cut = (source.width - cropped.width, source.height - cropped.height)
    return cropped.resize(size, Image.LANCZOS), cut[0], cut[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Картинки оформления бота")
    parser.add_argument("slot", choices=sorted(SLOTS))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    size, where = SLOTS[args.slot]
    source = Image.open(args.source).convert("RGB")
    picture, cut_x, cut_y = fit(source, size)

    share = max(cut_x / source.width, cut_y / source.height)
    BRAND.mkdir(parents=True, exist_ok=True)
    target = BRAND / f"{args.slot}.jpg"
    picture.save(target, quality=QUALITY, optimize=True)

    print(f"{target.relative_to(ROOT)}: {size[0]}×{size[1]} — {where}")
    print(f"  исходник {source.width}×{source.height}, срезано {cut_x}×{cut_y} px")
    if share > LOUD_CROP:
        print(f"  ВНИМАНИЕ: ушло {share:.0%} кадра — проверьте, не отрезало ли нужное")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
