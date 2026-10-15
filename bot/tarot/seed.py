"""Из чего складывается именно этот расклад.

Карты можно тянуть из криптографической случайности — так и было. Но тогда
расклад ничей: его нельзя повторить, и человек в нём не участвует. Здесь
случайность собирается из четырёх вещей: кто спрашивает, какое число он
назвал, когда спросил и о чём.

Никакой «точности предсказания» это не добавляет. Зато расклад становится
его собственным: то же число в ту же минуту даст те же карты, а чужое — нет.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from random import Random

# Минута — достаточно мелкое деление: два расклада подряд уже разойдутся,
# а повторить свой же можно, пока не прошла минута
MOMENT = "%Y-%m-%d %H:%M"


def mix(user_id: int, number: int, moment: datetime, question: str | None = None) -> int:
    source = f"{user_id}|{number}|{moment.strftime(MOMENT)}|{(question or '').strip().lower()}"
    return int.from_bytes(hashlib.blake2b(source.encode(), digest_size=16).digest(), "big")


def rng_for(user_id: int, number: int, moment: datetime, question: str | None = None) -> Random:
    return Random(mix(user_id, number, moment, question))
