"""Правила игры «карты угадывают».

Главное здесь — что оценка человека относится к ответу карты, а не к вопросу.
Ошибиться тут легко и незаметно: игра продолжит идти, просто бот будет
строить вопросы на перевёрнутых фактах и не угадает никогда.
"""

from __future__ import annotations

from bot import guess
from bot.guess import CARD_NO, CARD_YES, Game, Turn


def turn(question="Этот человек жив?", card_answer=CARD_YES, verdict=None, follow_up=False):
    return Turn(
        question=question,
        card="major_09",
        card_answer=card_answer,
        reading="Отшельник говорит своё",
        follow_up=follow_up,
        verdict=verdict,
    )


def test_the_person_grades_the_card_not_the_question():
    """«Нет» значит «карта ошиблась», то есть правда — обратное сказанному."""
    assert guess.truth(turn(card_answer=CARD_YES, verdict=guess.YES)) is True
    assert guess.truth(turn(card_answer=CARD_YES, verdict=guess.NO)) is False
    assert guess.truth(turn(card_answer=CARD_NO, verdict=guess.YES)) is False
    # Карта сказала «нет» и ошиблась — значит да
    assert guess.truth(turn(card_answer=CARD_NO, verdict=guess.NO)) is True


def test_a_shrug_establishes_nothing():
    assert guess.truth(turn(verdict=guess.MAYBE)) is None
    assert guess.truth(turn(verdict=guess.UNKNOWN)) is None
    assert guess.truth(turn(verdict=None)) is None


def test_facts_read_as_the_truth_not_as_the_cards_said():
    game = Game(
        turns=[
            turn("Этот человек жив?", CARD_YES, guess.NO),
            turn("Он мужчина?", CARD_NO, guess.NO),
            turn("Он из России?", CARD_YES, guess.MAYBE),
        ]
    )
    assert guess.facts(game) == [
        "Этот человек жив? — нет",
        "Он мужчина? — да",
        "Он из России? — человек не знает",
    ]


def test_an_ungraded_turn_is_not_a_fact():
    game = Game(turns=[turn(verdict=None)])
    assert guess.facts(game) == []
    assert game.pending is not None


def test_a_rejected_card_is_pressed_once_more():
    """Карта ошиблась — тему дожимают уточнением, но только одним."""
    assert guess.needs_follow_up(Game(turns=[turn(verdict=guess.NO)])) is True
    assert guess.needs_follow_up(Game(turns=[turn(verdict=guess.YES)])) is False
    # Уточнение к уточнению не делаем: иначе партия застрянет на одной оси
    assert guess.needs_follow_up(Game(turns=[turn(verdict=guess.NO, follow_up=True)])) is False


def test_nothing_to_press_at_the_start():
    assert guess.needs_follow_up(Game()) is False


def test_the_name_is_demanded_now_and_then_not_every_turn():
    """После порога бот обязан назвать имя — но не сыпать ими подряд."""
    early = Game(turns=[turn(verdict=guess.YES)] * (guess.GUESS_AFTER - 1))
    assert guess.must_guess(early) is False

    due = Game(turns=[turn(verdict=guess.YES)] * guess.GUESS_AFTER)
    assert guess.must_guess(due) is True

    # Назвал мимо — следующий раз не сразу
    missed = Game(turns=due.turns, guesses=["Фредди Меркьюри"])
    assert guess.must_guess(missed) is False
    assert guess.must_guess(Game(turns=[turn()] * (guess.GUESS_AFTER + 5), guesses=["Кто-то"]))


def test_a_player_out_of_guesses_is_not_asked_for_another():
    spent = Game(turns=[turn()] * 20, guesses=["Раз", "Два", "Три"])
    assert spent.out_of_guesses is True
    assert guess.must_guess(spent) is False


def test_the_party_has_an_end():
    assert Game(turns=[turn()] * guess.MAX_QUESTIONS).out_of_questions is True
    assert Game(turns=[turn()] * (guess.MAX_QUESTIONS - 1)).left == 1


# --- разбор ответа модели ---------------------------------------------------


def test_a_plain_object_is_read():
    step = guess.parse('{"question": "Он жив?", "reading": "Солнце светит — да"}')
    assert step.question == "Он жив?"
    assert step.reading == "Солнце светит — да"
    assert step.guessing is False


def test_a_fenced_object_is_read_too():
    step = guess.parse('```json\n{"guess": "Пушкин", "reading": "Звезда зовёт имя"}\n```')
    assert step.guessing is True
    assert step.guess == "Пушкин"


def test_an_object_buried_in_chatter_is_dug_out():
    step = guess.parse('Хорошо, вот мой ход:\n{"question": "Он поэт?"}\nНадеюсь, подойдёт.')
    assert step.question == "Он поэт?"


def test_nonsense_is_refused_rather_than_guessed_at():
    assert guess.parse("") is None
    assert guess.parse("совсем не json") is None
    assert guess.parse('{"reading": "карта легла"}') is None  # ни вопроса, ни имени
    assert guess.parse("[1, 2, 3]") is None


# --- подсказка модели -------------------------------------------------------


def test_the_prompt_carries_the_established_truth():
    game = Game(turns=[turn("Этот человек жив?", CARD_YES, guess.NO)])
    text = guess.prompt(game, "Отшельник", CARD_NO)

    assert "Этот человек жив? — нет" in text
    assert "Отшельник" in text
    assert f"Её ответ на твой вопрос: {CARD_NO}" in text


def test_the_first_prompt_says_the_table_is_empty():
    assert "это первый вопрос" in guess.prompt(Game(), "Шут", CARD_YES)


def test_the_prompt_asks_to_press_after_a_rejection():
    game = Game(turns=[turn(verdict=guess.NO)])
    assert "уточняющий вопрос" in guess.prompt(game, "Башня", CARD_YES)


def test_the_prompt_demands_a_name_when_it_is_time():
    game = Game(turns=[turn(verdict=guess.YES)] * guess.GUESS_AFTER)
    assert "Пора назвать имя" in guess.prompt(game, "Звезда", CARD_YES)


def test_the_prompt_lists_the_names_already_missed():
    game = Game(turns=[turn(verdict=guess.YES)], guesses=["Цой", "Высоцкий"])
    assert "Цой, Высоцкий" in guess.prompt(game, "Луна", CARD_YES)


# --- переход состояния ------------------------------------------------------


def guessing_turn(name="Фредди Меркьюри"):
    return Turn(
        question="",
        card="major_17",
        card_answer=CARD_YES,
        reading="Звезда называет имя",
        guess=name,
    )


def test_a_right_name_ends_the_party():
    game = Game(turns=[guessing_turn()])
    guess.grade(game, guess.YES)
    assert game.status == guess.WON
    assert game.guesses == []


def test_a_wrong_name_is_remembered_and_the_party_goes_on():
    game = Game(turns=[guessing_turn("Цой")])
    guess.grade(game, guess.NO)
    assert game.status == guess.ACTIVE
    assert game.guesses == ["Цой"]


def test_a_missed_name_is_not_a_fact_about_the_person():
    game = Game(turns=[guessing_turn("Цой"), turn("Он музыкант?", CARD_YES, guess.YES)])
    game.turns[0].verdict = guess.NO
    assert guess.facts(game) == ["Он музыкант? — да"]


def test_a_name_takes_only_yes_or_no():
    for soft in (guess.MAYBE, guess.UNKNOWN):
        game = Game(turns=[guessing_turn()])
        try:
            guess.grade(game, soft)
        except ValueError:
            continue
        raise AssertionError(f"«{soft}» на догадку должно отклоняться")


def test_a_follow_up_takes_only_yes_or_no():
    game = Game(turns=[turn(follow_up=True)])
    try:
        guess.grade(game, guess.MAYBE)
    except ValueError:
        return
    raise AssertionError("на уточнение «возможно» отвечать нельзя")


def test_nothing_to_grade_is_an_error_not_a_silent_no_op():
    game = Game(turns=[turn(verdict=guess.YES)])
    try:
        guess.grade(game, guess.YES)
    except ValueError:
        return
    raise AssertionError("оценка без ждущего хода должна отклоняться")


def test_the_bot_concedes_when_the_budget_runs_out():
    game = Game(turns=[turn(verdict=guess.YES)] * (guess.MAX_QUESTIONS - 1) + [turn()])
    guess.grade(game, guess.YES)
    assert game.status == guess.LOST


def test_the_bot_concedes_after_the_last_wrong_name():
    game = Game(turns=[guessing_turn("Три")], guesses=["Раз", "Два"])
    guess.grade(game, guess.NO)
    assert game.guesses == ["Раз", "Два", "Три"]
    assert game.status == guess.LOST


def test_a_rejected_name_does_not_start_a_follow_up():
    """Уточнять нечего: промах по имени — не ось, которую можно дожать."""
    game = Game(turns=[guessing_turn()])
    guess.grade(game, guess.NO)
    assert guess.needs_follow_up(game) is False
