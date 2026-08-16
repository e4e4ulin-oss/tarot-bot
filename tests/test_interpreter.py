from random import Random

import pytest

from bot.services.grok import GrokClient, GrokError
from bot.services.interpreter import Interpreter, build_prompt, to_telegram_html
from bot.tarot import draw_spread, get_spread
from bot.tarot.deck import get_card
from bot.tarot.draw import DrawnCard


def test_prompt_contains_cards_positions_and_question():
    spread = get_spread("ptf")
    drawn = draw_spread(spread, rng=Random(11))
    prompt = build_prompt(spread, drawn, "Что мешает переезду?")
    assert "Что мешает переезду?" in prompt
    for item in drawn:
        assert item.position in prompt
        assert item.card.name in prompt


def test_prompt_without_question_asks_for_general_reading():
    spread = get_spread("day")
    prompt = build_prompt(spread, draw_spread(spread, rng=Random(2)), None)
    assert "не задал конкретного вопроса" in prompt


def test_yes_no_prompt_includes_preliminary_verdict():
    spread = get_spread("yes_no")
    drawn = [DrawnCard(position="Ответ", card=get_card("major_16"), reversed=False)]
    prompt = build_prompt(spread, drawn, "Переезжать?")
    assert "Скорее нет" in prompt


def test_to_telegram_html_strips_markdown_and_escapes():
    raw = "**Общая картина**\nВас ждёт <перемена> & рост\n\n## Совет: не спешите"
    html = to_telegram_html(raw)
    assert "*" not in html and "#" not in html
    assert "&lt;перемена&gt;" in html
    assert "&amp;" in html
    assert "<b>Общая картина</b>" in html
    assert "<b>Совет</b>" in html


@pytest.mark.asyncio
async def test_interpret_falls_back_to_local_meanings_without_api_key():
    interpreter = Interpreter(GrokClient(api_key=""))
    spread = get_spread("sit")
    drawn = draw_spread(spread, rng=Random(9))
    result = await interpreter.interpret(spread, drawn, "Что делать?")
    assert result.ai_used is False
    for item in drawn:
        assert item.position in result.text


@pytest.mark.asyncio
async def test_interpret_falls_back_when_grok_fails(monkeypatch):
    client = GrokClient(api_key="test-key", retries=0)

    async def boom(*args, **kwargs):
        raise GrokError("нет связи")

    monkeypatch.setattr(client, "complete", boom)

    spread = get_spread("day")
    drawn = draw_spread(spread, rng=Random(4))
    result = await Interpreter(client).interpret(spread, drawn, None)
    assert result.ai_used is False
    assert result.text


@pytest.mark.asyncio
async def test_interpret_uses_grok_answer_when_available(monkeypatch):
    client = GrokClient(api_key="test-key")

    async def fake(system, user, **kwargs):
        assert "таролог" in system
        return "Прошлое - было сложно.\n\nОбщая картина\nВсё выправляется."

    monkeypatch.setattr(client, "complete", fake)

    spread = get_spread("ptf")
    drawn = draw_spread(spread, rng=Random(6))
    result = await Interpreter(client).interpret(spread, drawn, "Как дела?")
    assert result.ai_used is True
    assert "<b>Общая картина</b>" in result.text
