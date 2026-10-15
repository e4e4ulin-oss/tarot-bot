"""Мелкие утилиты."""

from __future__ import annotations

from datetime import UTC, datetime


def split_text(text: str, limit: int = 3900) -> list[str]:
    """Режет длинный текст на части по границам абзацев, не ломая HTML-теги внутри строки."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            cut = paragraph.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip("\n")
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def fmt_date(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.strftime("%d.%m.%Y %H:%M")


MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def fmt_day(value: datetime, today: datetime | None = None) -> str:
    """День по-русски: «16 октября», с годом — если он не этот.

    Для транзита важен день, а не час: планета стоит на градусе неделями.
    """
    today = today or datetime.now(UTC)
    day = f"{value.day} {MONTHS[value.month - 1]}"
    if value.date() == today.date():
        return "сегодня"
    return day if value.year == today.year else f"{day} {value.year}"
