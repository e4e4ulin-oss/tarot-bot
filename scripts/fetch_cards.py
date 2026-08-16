#!/usr/bin/env python3
"""Скачивает 78 сканов колоды Райдера-Уэйта с Викисклада в bot/tarot/data/cards/.

Запускать не обязательно: по умолчанию бот отдаёт Telegram ссылку на Викисклад
и кэширует полученный file_id. Скрипт нужен, если картинки хочется держать
рядом с кодом — например, чтобы бот не зависел от внешнего сайта.

    python scripts/fetch_cards.py

Изображения Памелы Колман Смит (1909) находятся в общественном достоянии.
Свои фотографии колоды можно просто положить в тот же каталог под именами
вида major_00.jpg, cups_01.jpg — они имеют приоритет над сканами.
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.tarot import load_deck  # noqa: E402
from bot.tarot.images import LOCAL_DIR, commons_url  # noqa: E402

USER_AGENT = "tarot-bot/1.0 (https://github.com/e4e4ulin-oss/tarot-bot)"
PAUSE = 1.0  # Викисклад отвечает 429 на слишком частые запросы


def main() -> int:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    deck = load_deck()
    failed: list[str] = []

    for index, card in enumerate(deck, start=1):
        target = LOCAL_DIR / f"{card.id}.jpg"
        if target.exists():
            print(f"[{index:2}/78] {card.id}: уже скачана")
            continue

        request = urllib.request.Request(commons_url(card), headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                target.write_bytes(response.read())
            print(f"[{index:2}/78] {card.id}: {target.stat().st_size // 1024} КБ")
        except Exception as exc:  # noqa: BLE001 - причина печатается и идём дальше
            failed.append(f"{card.id}: {exc}")
            print(f"[{index:2}/78] {card.id}: не скачалась — {exc}")
        time.sleep(PAUSE)

    if failed:
        print("\nНе скачались:")
        for line in failed:
            print(" ", line)
        print("Повторите запуск — скачанное пропускается.")
        return 1

    print(f"\nГотово: {len(deck)} карт в {LOCAL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
