"""Автоматическая интерпретация расклада: Grok поверх собственной базы значений."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import escape

from ..tarot import DrawnCard, Spread, cards_plain, format_meanings
from ..tarot.draw import yes_no_verdict
from .grok import GrokClient, GrokError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты — таролог-консультант в Telegram-боте. Ты пишешь по-русски, тепло, спокойно и по делу.

Как ты работаешь:
— Опираешься ровно на те карты и позиции, которые тебе дали. Не добавляешь и не заменяешь карты.
— Значения карт из базы — твоя опора, но ты связываешь их между собой и с вопросом человека,
  а не пересказываешь по отдельности.
— Говоришь о тенденциях и о том, что человек может сделать, а не о неизбежной судьбе.
— Никакого запугивания, фатализма и «порчи». Никаких предсказаний смерти, болезней, диагнозов,
  беременности, исхода судебных дел и другой темы, где нужен врач, юрист или психолог:
  в таких случаях мягко говоришь, что карты показывают состояние и настрой, и советуешь
  обратиться к специалисту.
— Обращаешься к человеку на «вы», без панибратства и без эзотерического пафоса.

Формат ответа:
— Обычный текст без markdown, без символов *, #, _ и без эмодзи в начале строк.
— Сначала короткий разбор по каждой позиции (1–3 предложения на карту, начинай строку
  с названия позиции и тире).
— Потом абзац «Общая картина» — как карты складываются в одну историю.
— В конце абзац «Совет» — 2–3 конкретных, выполнимых шага.
— Всего 250–450 слов. Не повторяй список карт в начале ответа.
"""

_MARKDOWN_NOISE = re.compile(r"[*_#`]+")
_MULTI_BLANK = re.compile(r"\n{3,}")

DISCLAIMER = (
    "Расклад носит развлекательный характер и не заменяет консультацию врача, юриста или психолога."
)


@dataclass(frozen=True, slots=True)
class Interpretation:
    text: str  # готовый HTML для Telegram
    ai_used: bool


def build_prompt(
    spread: Spread, drawn: list[DrawnCard], question: str | None, author_name: str = ""
) -> str:
    parts = [f"Расклад: «{spread.title}» ({spread.subtitle})."]
    parts.append(
        f"Вопрос человека: {question}"
        if question
        else "Человек не задал конкретного вопроса — сделайте разбор общей ситуации."
    )
    parts.append("\nВыпавшие карты (позиция, карта, положение, ключевые слова, базовое значение):")
    parts.append(cards_plain(drawn))
    if spread.yes_no and drawn:
        parts.append(
            f"\nПредварительный вердикт по полярности карты: {yes_no_verdict(drawn[0])}. "
            "Объясните его и укажите, от чего он зависит."
        )
    if spread.size == 1:
        parts.append("\nОдна карта — ответ должен быть коротким: 120–200 слов.")
    parts.append("\nНапишите разбор по формату из системной инструкции.")
    return "\n".join(parts)


def to_telegram_html(raw: str) -> str:
    """Grok просят отвечать обычным текстом; на всякий случай чистим разметку и экранируем."""
    cleaned = _MARKDOWN_NOISE.sub("", raw).strip()
    cleaned = _MULTI_BLANK.sub("\n\n", cleaned)
    lines: list[str] = []
    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        heading = re.match(r"^(Общая картина|Совет|Итог|Вывод)\s*[:.—-]?\s*(.*)$", stripped)
        if heading:
            rest = heading.group(2)
            lines.append(
                f"<b>{escape(heading.group(1))}</b>" + (f"\n{escape(rest)}" if rest else "")
            )
        else:
            lines.append(escape(stripped))
    return "\n".join(lines).strip()


class Interpreter:
    """Делает разбор через Grok, а если он недоступен — собирает его из базы значений."""

    def __init__(self, grok: GrokClient, *, author_name: str = "") -> None:
        self.grok = grok
        self.author_name = author_name

    async def interpret(
        self, spread: Spread, drawn: list[DrawnCard], question: str | None
    ) -> Interpretation:
        if self.grok.enabled:
            try:
                raw = await self.grok.complete(
                    SYSTEM_PROMPT, build_prompt(spread, drawn, question, self.author_name)
                )
                return Interpretation(text=to_telegram_html(raw), ai_used=True)
            except GrokError as exc:
                logger.warning("Разбор через Grok не получился, отдаём базовые значения: %s", exc)

        return Interpretation(text=self.fallback(drawn), ai_used=False)

    @staticmethod
    def fallback(drawn: list[DrawnCard]) -> str:
        return format_meanings(drawn)
