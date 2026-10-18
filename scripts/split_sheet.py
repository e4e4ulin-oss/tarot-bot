"""Режет лист с несколькими сценами на отдельные карты.

За один запрос генератор отдаёт одну картинку, поэтому сюжетные младшие карты
рисуются по четыре в сетке 2×2: один свет, одна палитра, вчетверо меньше заходов.
Здесь лист режется на ячейки, каждая растягивается до размера карты и проходит
обычную приёмку.

    python scripts/split_sheet.py ЛИСТ.png wands_02 wands_03 wands_04 wands_05

Имена перечисляются слева направо и сверху вниз. Ячейка, не прошедшая приёмку,
не сохраняется — её печатает в отчёте, и перерисовывать придётся весь лист.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_cards import inspect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "docs" / "design" / "cards"
BOARD = ROOT / "miniapp" / "cards"
DECK = ROOT / "miniapp" / "cards.json"

CARD_SIZE = (1024, 1536)
BOARD_SIZE = (720, 1080)

# Шов между ячейками генератор оставляет светлым; без этого запаса он попадает
# в замер первой строкой и выглядит как фигура у самого края
SEAM = 18

SUIT_TONES = {
    "wands": "#6D1A01",
    "cups": "#123A4A",
    "swords": "#29232B",
    "pentacles": "#262813",
}


def tone_for(card_id: str) -> str:
    suit = card_id.split("_")[0]
    if suit not in SUIT_TONES:
        raise SystemExit(f"не знаю тона для {card_id}")
    return SUIT_TONES[suit]


def cells(sheet: Image.Image, count: int) -> list[Image.Image]:
    if count != 4:
        raise SystemExit("пока умею только сетку 2×2")
    width, height = sheet.size
    out = []
    for row in range(2):
        for column in range(2):
            out.append(
                sheet.crop(
                    (
                        column * width // 2 + (SEAM if column else 0),
                        row * height // 2 + (SEAM if row else 0),
                        (column + 1) * width // 2 - (0 if column else SEAM),
                        (row + 1) * height // 2 - (0 if row else SEAM),
                    )
                )
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Разрезать лист на карты")
    parser.add_argument("sheet", type=Path)
    parser.add_argument("names", nargs="+", help="слева направо, сверху вниз")
    parser.add_argument("--force", action="store_true", help="сохранить и непрошедшие")
    args = parser.parse_args()

    sheet = Image.open(args.sheet).convert("RGB")
    pieces = cells(sheet, len(args.names))

    CARDS.mkdir(parents=True, exist_ok=True)
    BOARD.mkdir(parents=True, exist_ok=True)
    known = {c["id"] for c in json.loads(DECK.read_text(encoding="utf-8"))}

    failed = []
    for name, cell in zip(args.names, pieces, strict=True):
        if name not in known:
            raise SystemExit(f"нет такой карты в колоде: {name}")
        card = cell.resize(CARD_SIZE, Image.LANCZOS)
        work = Path("/tmp") / f"{name}-check.png"
        card.save(work)
        check = inspect(work, tone_for(name))
        print(f"{name}: {check.report}")
        if not check.ok and not args.force:
            failed.append(name)
            continue
        card.save(CARDS / f"{name}.png")
        card.resize(BOARD_SIZE, Image.LANCZOS).save(
            BOARD / f"{name}.jpg", quality=86, optimize=True
        )

    if failed:
        print("не принято:", ", ".join(failed))
        return 1
    print(f"принято карт: {len(args.names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
