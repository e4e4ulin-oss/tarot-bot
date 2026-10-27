"""Ход партии: что служба делает с ответом модели и с её молчанием.

Правила игры проверяются отдельно и без сети. Здесь только стык с моделью —
то место, где ответ приходит в непредсказуемом виде.
"""

from __future__ import annotations

import pytest

from bot import guess
from bot.guess import Game, Turn
from bot.services.grok import GrokClient, GrokError
from bot.services.guessing import Guessing


def client(*answers: str) -> GrokClient:
    """Клиент, который отдаёт заготовленные ответы по очереди."""
    grok = GrokClient(api_key="test-key")
    queue = list(answers)

    async def complete(system: str, user: str, **kwargs) -> str:
        grok.asked.append(user)
        return queue.pop(0) if queue else answers[-1]

    grok.asked = []  # type: ignore[attr-defined]
    grok.complete = complete  # type: ignore[method-assign]
    return grok


@pytest.mark.asyncio
async def test_a_turn_carries_the_card_the_question_and_the_reading():
    grok = client('{"question": "Этот человек жив?", "reading": "Солнце светит — да"}')
    turn = await Guessing(grok).step(Game())

    assert turn.question == "Этот человек жив?"
    assert turn.reading == "Солнце светит — да"
    assert turn.card  # карта вытянута, а не выдумана моделью
    assert turn.card_answer in (guess.CARD_YES, guess.CARD_NO)
    assert turn.verdict is None  # ход ждёт оценки человека


@pytest.mark.asyncio
async def test_the_model_sees_the_card_and_its_answer():
    """Карта тянется до вопроса к модели: её дело — прочитать, а не выбрать."""
    grok = client('{"question": "Он поэт?", "reading": "Карта согласна"}')
    turn = await Guessing(grok).step(Game())

    asked = grok.asked[0]  # type: ignore[attr-defined]
    assert f"Её ответ на твой вопрос: {turn.card_answer}" in asked


@pytest.mark.asyncio
async def test_a_name_comes_back_as_a_guessing_turn():
    grok = client('{"guess": "Фредди Меркьюри", "reading": "Звезда называет имя"}')
    turn = await Guessing(grok).step(Game())

    assert turn.is_guess is True
    assert turn.guess == "Фредди Меркьюри"
    assert turn.follow_up is False


@pytest.mark.asyncio
async def test_a_rejected_card_makes_the_next_turn_a_follow_up():
    game = Game(
        turns=[
            Turn(
                question="Он жив?",
                card="major_09",
                card_answer=guess.CARD_YES,
                reading="",
                verdict=guess.NO,
            )
        ]
    )
    grok = client('{"question": "Он умер в XX веке?", "reading": "Карта кивает"}')
    turn = await Guessing(grok).step(game)

    assert turn.follow_up is True
    assert "уточняющий вопрос" in grok.asked[0]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_prose_instead_of_an_object_is_retried_then_refused():
    grok = client("Думаю, стоит спросить про эпоху.", "И правда, давайте про эпоху.")
    with pytest.raises(GrokError):
        await Guessing(grok).step(Game())
    # Один повтор был, бесконечного кружения не было
    assert len(grok.asked) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_second_try_that_parses_is_accepted():
    grok = client("сначала проза", '{"question": "Он музыкант?", "reading": "Да"}')
    turn = await Guessing(grok).step(Game())
    assert turn.question == "Он музыкант?"


@pytest.mark.asyncio
async def test_without_a_model_there_is_no_game():
    """Заготовки из базы значений тут не спасают: вопрос придумать некому."""
    with pytest.raises(GrokError):
        await Guessing(GrokClient(api_key="")).step(Game())
