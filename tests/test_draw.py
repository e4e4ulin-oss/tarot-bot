from random import Random

import pytest

from bot.tarot import SPREADS, deserialize, draw_spread, format_cards, get_spread, serialize
from bot.tarot.deck import get_card
from bot.tarot.draw import DrawnCard, card_polarity, yes_no_verdict


@pytest.mark.parametrize("key", list(SPREADS))
def test_draw_matches_spread_size_and_has_no_duplicates(key):
    spread = get_spread(key)
    drawn = draw_spread(spread, rng=Random(42))
    assert len(drawn) == spread.size
    assert [d.position for d in drawn] == list(spread.positions)
    assert len({d.card.id for d in drawn}) == spread.size


def test_reversals_can_be_disabled():
    spread = get_spread("celtic")
    drawn = draw_spread(spread, allow_reversed=False, rng=Random(1))
    assert all(not d.reversed for d in drawn)


def test_reversed_chance_is_respected():
    spread = get_spread("celtic")
    always = draw_spread(spread, reversed_chance=1.0, rng=Random(7))
    never = draw_spread(spread, reversed_chance=0.0, rng=Random(7))
    assert all(d.reversed for d in always)
    assert all(not d.reversed for d in never)


def test_serialize_roundtrip():
    drawn = draw_spread(get_spread("ptf"), rng=Random(3))
    restored = deserialize(serialize(drawn))
    assert [(d.position, d.card.id, d.reversed) for d in restored] == [
        (d.position, d.card.id, d.reversed) for d in drawn
    ]


def test_yes_no_verdict_flips_with_reversal():
    sun = DrawnCard(position="Ответ", card=get_card("major_19"), reversed=False)
    assert card_polarity(sun.card) == 1
    assert yes_no_verdict(sun) == "Скорее да"

    sun_reversed = DrawnCard(position="Ответ", card=get_card("major_19"), reversed=True)
    assert yes_no_verdict(sun_reversed) == "Скорее нет"

    fool = DrawnCard(position="Ответ", card=get_card("major_00"), reversed=False)
    assert yes_no_verdict(fool) == "Ответ пока не определён"


def test_format_cards_includes_question_and_verdict():
    spread = get_spread("yes_no")
    drawn = [DrawnCard(position="Ответ", card=get_card("major_19"), reversed=False)]
    text = format_cards(spread, drawn, "Стоит ли начинать?")
    assert "Стоит ли начинать?" in text
    assert "Скорее да" in text
    assert "Солнце" in text


def test_format_cards_escapes_html_in_question():
    spread = get_spread("day")
    drawn = draw_spread(spread, rng=Random(5))
    text = format_cards(spread, drawn, "<b>хак</b>")
    assert "<b>хак</b>" not in text
    assert "&lt;b&gt;хак&lt;/b&gt;" in text
