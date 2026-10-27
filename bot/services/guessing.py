"""Ведение партии «карты угадывают»: карта тянется здесь, слова берутся у модели.

Разделение намеренное. Правила игры лежат в bot.guess и не знают ни про сеть,
ни про базу — их целиком проверяют тесты. Здесь только то, что без внешнего
мира не работает: обращение к модели и его неудачи.

Без модели игры нет. Заготовка из базы значений карт, как у разбора, тут не
спасает: придумать следующий вопрос и назвать имя может только она.
"""

from __future__ import annotations

import asyncio
import logging

from .. import guess
from ..guess import Game, Turn
from ..tarot.draw import DrawnCard
from .grok import GrokClient, GrokError

logger = logging.getLogger(__name__)

# Модель иногда отвечает прозой вместо объекта. Один повтор дешевле, чем
# оборванная партия, но и зацикливаться на этом не стоит.
RETRIES = 1


class Guessing:
    """Один ход партии: вытянуть карту и спросить модель, что дальше."""

    def __init__(self, grok: GrokClient, *, deadline: float = 45.0) -> None:
        self.grok = grok
        self.deadline = deadline

    @property
    def enabled(self) -> bool:
        return self.grok.enabled

    async def step(
        self,
        game: Game,
        *,
        allow_reversed: bool = True,
        reversed_chance: float = 0.4,
    ) -> Turn:
        """Следующий ход. Карта тянется до обращения к модели — и честно."""
        if not self.grok.enabled:
            raise GrokError("модель недоступна")

        card, answer = guess.decisive(
            allow_reversed=allow_reversed, reversed_chance=reversed_chance
        )
        step = await self._ask(game, card, answer)

        return Turn(
            question=step.question,
            names=list(step.names),
            card=card.card.id,
            reversed=card.reversed,
            card_answer=answer,
            reading=step.reading,
            # Уточняют вопрос, а не имена: дожимать нечего, если карты
            # показывают типаж
            follow_up=guess.needs_follow_up(game) and not step.names,
        )

    async def _ask(self, game: Game, card: DrawnCard, answer: str) -> guess.Step:
        text = guess.prompt(game, card.title, answer)
        last = ""
        for attempt in range(RETRIES + 1):
            raw = await asyncio.wait_for(
                self.grok.complete(guess.SYSTEM, text), timeout=self.deadline
            )
            parsed = guess.parse(raw)
            if parsed is not None:
                return parsed
            last = raw[:200]
            logger.warning("Ход не разобран (попытка %s): %s", attempt + 1, last)
        raise GrokError(f"модель ответила не по форме: {last}")
