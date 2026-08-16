"""Форматирование расклада для отправки в Telegram (HTML parse mode)."""

from __future__ import annotations

from html import escape

from .draw import DrawnCard, yes_no_verdict
from .spreads import Spread


def format_cards(spread: Spread, drawn: list[DrawnCard], question: str | None = None) -> str:
    lines = [f"🔮 <b>{escape(spread.title)}</b>"]
    if question:
        lines.append(f"<i>Вопрос: {escape(question)}</i>")
    lines.append("")

    single = spread.size == 1
    for index, item in enumerate(drawn, start=1):
        prefix = "" if single else f"{index}. "
        lines.append(f"{prefix}<b>{escape(item.position)}</b>")
        lines.append(f"{escape(item.title)}")
        lines.append(f"<i>{escape(' · '.join(item.keywords))}</i>")
        lines.append("")

    if spread.yes_no and drawn:
        lines.append(f"<b>{escape(yes_no_verdict(drawn[0]))}</b>")
        lines.append("")

    return "\n".join(lines).strip()


def format_meanings(drawn: list[DrawnCard]) -> str:
    """Резервный разбор из базы значений — используется, если ИИ недоступен."""
    blocks = []
    for item in drawn:
        blocks.append(
            f"<b>{escape(item.position)}</b> — {escape(item.card.name)}\n{escape(item.meaning)}"
        )
    advice = next((d.card.advice for d in drawn if d.card.advice and not d.reversed), "")
    text = "\n\n".join(blocks)
    if advice:
        text += f"\n\n<b>Совет:</b> {escape(advice)}"
    return text


def cards_plain(drawn: list[DrawnCard]) -> str:
    """Компактное текстовое описание расклада — для промпта ИИ и для уведомлений."""
    return "\n".join(
        f"{item.position}: {item.card.name}"
        f"{' (перевёрнутая)' if item.reversed else ' (прямая)'} — "
        f"{', '.join(item.keywords)}. {item.meaning}"
        for item in drawn
    )
