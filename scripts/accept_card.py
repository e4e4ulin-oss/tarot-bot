"""Принимает нарисованную карту: проверяет, чинит типовое, раскладывает по местам.

Одна команда вместо пяти ручных шагов, которые повторяются на каждой из 78 карт:

    python scripts/accept_card.py major_03 ~/Downloads/картинка.png

Что делает:
  1. меряет карту по правилам своей группы (фон, поле под надпись, место фигуры);
  2. если не прошла — чинит типовое: опускает фигуру, приводит фон к цвету группы,
     и меряет снова;
  3. кладёт полный размер в docs/design/cards, уменьшенную копию для доски рядом;
  4. отмечает карту нарисованной в манифесте.

Карту, которую починить нельзя, не сохраняет: такую надо перерисовать.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fit_title import DEFAULT_START, lower  # noqa: E402
from fix_ground import ground_mask, recolour  # noqa: E402
from generate_cards import MANIFEST, hex_to_rgb, inspect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "docs" / "design" / "cards"
BOARD = ROOT.parent / "miniapp" / "cards"
BOARD_SIZE = (720, 1080)


def card_by_id(card_id: str) -> dict:
    for card in json.loads(MANIFEST.read_text(encoding="utf-8")):
        if card["id"] == card_id:
            return card
    raise SystemExit(f"нет такой карты в манифесте: {card_id}")


def mark_done(card_id: str) -> None:
    cards = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for card in cards:
        if card["id"] == card_id:
            card["done"] = True
    MANIFEST.write_text(
        json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def repair(image: Image.Image, tone_hex: str, report: str) -> tuple[Image.Image, list[str]]:
    """Типовые починки — ровно те, что уже случались не по одному разу."""
    done: list[str] = []

    if "фигура начинается" in report or "поле под надпись" in report:
        lowered = lower(image, DEFAULT_START)
        if lowered is not image:
            image = lowered
            done.append("фигура опущена под поле надписи")

    if "фон разошёлся" in report:
        share = sum(1 for v in ground_mask(image).getdata() if v > 200) / (
            image.width * image.height
        )
        if share > 0.75:
            done.append("фон перекрасить нельзя: фигура написана в цвет фона")
        else:
            image = recolour(image, hex_to_rgb(tone_hex))
            done.append("фон приведён к цвету группы")

    return image, done


def main() -> int:
    parser = argparse.ArgumentParser(description="Принять нарисованную карту")
    parser.add_argument("card_id")
    parser.add_argument("image", type=Path)
    parser.add_argument("--force", action="store_true", help="сохранить даже без приёмки")
    args = parser.parse_args()

    card = card_by_id(args.card_id)
    tone = card["tone"]["hex"]
    image = Image.open(args.image).convert("RGB")

    work = Path("/tmp") / f"{args.card_id}-check.png"
    image.save(work)
    check = inspect(work, tone)
    print(f"{card['title']}: {check.report}")

    if not check.ok:
        image, repairs = repair(image, tone, check.report)
        for line in repairs:
            print(f"  · {line}")
        image.save(work)
        check = inspect(work, tone)
        print(f"  после починки: {check.report}")

    if not check.ok and not args.force:
        print("  не принято — эту карту нужно перерисовать")
        return 1

    CARDS.mkdir(parents=True, exist_ok=True)
    BOARD.mkdir(parents=True, exist_ok=True)
    image.save(CARDS / f"{args.card_id}.png")
    image.resize(BOARD_SIZE, Image.LANCZOS).save(
        BOARD / f"{args.card_id}.jpg", quality=86, optimize=True
    )
    mark_done(args.card_id)
    print(f"  принято → docs/design/cards/{args.card_id}.png и на доску")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
