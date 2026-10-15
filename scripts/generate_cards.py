"""Рисует карты колоды через API OpenAI и сам проверяет результат.

Приёмка здесь не формальность: карта, у которой фон разошёлся с рубашкой или
верхняя четверть заросла волосами, сломает подпись и выбьется из колоды. Такая
карта не сохраняется, а перерисовывается — до трёх попыток, после чего остаётся
в отчёте как требующая внимания.

    export OPENAI_API_KEY=...
    python scripts/generate_cards.py --out docs/design/cards
    python scripts/generate_cards.py --only major_00 major_01 --tries 2
    python scripts/generate_cards.py --dry-run          # показать, что будет сделано

Эталон для каждой группы берётся из docs/design/reference: модель получает его
картинкой, иначе карты расходятся по тону уже к третьей.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageStat

API_URL = "https://api.openai.com/v1/images/edits"
MODEL = "gpt-image-1"
SIZE = "1024x1536"

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "design" / "prompts" / "majors.json"
REFERENCE = ROOT / "docs" / "design" / "reference"

# Эталон на группу: по нему модель держит руку, по нему же считается приёмка
REFERENCES = {
    "fool": "magician-crimson.jpg",
    "major1": "magician-crimson.jpg",
    "major2": "justice-indigo.jpg",
    "major3": "devil-charcoal.jpg",
}

COMMON = """\
Приложена карта моей колоды. Сделай карту из той же колоды. Вертикаль 2:3.

Фон: ровная плоскость цвета {hex} ({label}) с фактурой холста и мягкой виньеткой \
к углам. Ни пейзажа, ни интерьера, ни предметов на фоне — только фигура и её атрибуты.

Живопись гладкая, аэрографная, мазок почти не виден. Тело вылеплено мягким светом \
сбоку-сверху, контровой по контуру. Волосы и ткань прописаны длинными текучими прядями. \
Единственный металл — латунь #B98A3C, матовая, без бликов; её примерно десятая часть кадра.

Фон светлее одежды: ткань на фигуре заметно темнее фона, чтобы силуэт читался и не \
сливался с ним. Фон ровно того цвета, что назван выше, — не темнее. При этом на фигуру падает \
ясный свет: открытая кожа и верхние складки ткани заметно светлее фона, фигура вылеплена \
светом, а не тонет в темноте.

Героиня — женщина, как и на приложенной карте: та же рыжая грива, тот же тип лица и \
сложения, чтобы колода читалась как одна серия. Исключения только там, где этого требует \
сам сюжет.

Верхняя четверть карты — пустое ровное поле цвета фона. Ни фигуры, ни волос, ни ткани, \
ни атрибутов туда заходить не должно.

Сюжет: {subject}

Без текста, букв, цифр, рамок и водяных знаков.\
"""

# Границы приёмки — те же, по которым принимались Правосудие и рубашка
MAX_BACKGROUND_DELTA = 14.0
MAX_TITLE_SPREAD = 12.0
FIGURE_RANGE = (0.18, 0.34)
# Ниже этого запаса по светлому фигура перестаёт лепиться светом и на доске
# превращается в пятно. Перерисовку не требует — но знать об этом надо.
MIN_FIGURE_RELIEF = 10.0


@dataclass(frozen=True)
class Check:
    ok: bool
    report: str


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def inspect(path: Path, tone_hex: str) -> Check:
    """Фон, чистота поля под надпись и место, где начинается фигура."""
    image = Image.open(path).convert("RGB")
    width, height = image.size

    corner = ImageStat.Stat(image.crop((0, 0, width // 6, height // 6))).mean
    target = hex_to_rgb(tone_hex)
    delta = sum(abs(a - b) for a, b in zip(corner[:3], target, strict=True)) / 3

    title = image.crop((0, int(height * 0.08), width, int(height * 0.20)))
    spread = max(ImageStat.Stat(title).stddev)

    figure_at = 1.0
    for y in range(0, height, 6):
        row = image.crop((0, y, width, y + 6)).resize((80, 1))
        far = max(
            sum(abs(p[i] - corner[i]) for i in range(3)) / 3 for p in row.getdata()
        )
        if far > 45:
            figure_at = y / height
            break

    lower = image.crop((0, int(height * 0.35), width, height)).resize((80, 80))
    bright = sorted(sum(p) / 3 for p in lower.getdata())
    relief = bright[int(len(bright) * 0.9)] - sum(corner[:3]) / 3

    problems = []
    if delta > MAX_BACKGROUND_DELTA:
        problems.append(f"фон разошёлся на {delta:.1f}")
    if spread > MAX_TITLE_SPREAD:
        problems.append(f"поле под надпись шумное ({spread:.1f})")
    if not FIGURE_RANGE[0] <= figure_at <= FIGURE_RANGE[1]:
        problems.append(f"фигура начинается на {figure_at:.0%}")

    report = (
        f"фон Δ{delta:4.1f} · поле {spread:4.1f} · фигура с {figure_at:.0%}"
        f" · свет {relief:5.1f}"
        + (" — " + "; ".join(problems) if problems else "")
        + ("" if relief >= MIN_FIGURE_RELIEF else " (внимание: мало света на фигуре)")
    )
    return Check(not problems, report)


def draw(client: httpx.Client, key: str, prompt: str, reference: Path) -> bytes:
    with reference.open("rb") as handle:
        response = client.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}"},
            data={"model": MODEL, "prompt": prompt, "size": SIZE, "quality": "high", "n": 1},
            files={"image[]": (reference.name, handle, "image/jpeg")},
            timeout=300.0,
        )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return base64.b64decode(response.json()["data"][0]["b64_json"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Генерация карт колоды")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "design" / "cards")
    parser.add_argument("--only", nargs="*", help="идентификаторы карт, например major_00")
    parser.add_argument("--tries", type=int, default=3, help="попыток на карту")
    parser.add_argument("--redraw-done", action="store_true", help="перерисовать и готовые")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cards = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.only:
        cards = [c for c in cards if c["id"] in set(args.only)]
    if not args.redraw_done:
        cards = [c for c in cards if not c["done"]]
    if not cards:
        print("нечего рисовать")
        return 0

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key and not args.dry_run:
        print("нет OPENAI_API_KEY: положите ключ в переменные окружения")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []

    with httpx.Client() as client:
        for card in cards:
            tone = card["tone"]
            prompt = COMMON.format(hex=tone["hex"], label=tone["label"], subject=card["subject"])
            reference = REFERENCE / REFERENCES[tone["key"]]
            print(f"\n{card['id']} · {card['title']} · фон {tone['hex']}")

            if args.dry_run:
                print(f"  эталон: {reference.name}; промт {len(prompt)} знаков")
                continue

            target = args.out / f"{card['id']}.png"
            for attempt in range(1, args.tries + 1):
                try:
                    data = draw(client, key, prompt, reference)
                except (httpx.HTTPError, RuntimeError) as exc:
                    print(f"  попытка {attempt}: не нарисовалось — {exc}")
                    continue

                target.write_bytes(data)
                check = inspect(target, tone["hex"])
                print(f"  попытка {attempt}: {check.report}")
                if check.ok:
                    print(f"  принято → {target}")
                    break
            else:
                failed.append(card["id"])
                print("  не прошло приёмку, оставляю последнюю попытку для просмотра")

    if failed:
        print("\nтребуют внимания:", ", ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
