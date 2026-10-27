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
    assert guess.must_name(early) is False

    due = Game(turns=[turn(verdict=guess.YES)] * guess.GUESS_AFTER)
    assert guess.must_name(due) is True

    # Назвал мимо — следующий раз не сразу
    missed = Game(turns=due.turns, missed=["Фредди Меркьюри"])
    assert guess.must_name(missed) is False
    assert guess.must_name(Game(turns=[turn()] * (guess.GUESS_AFTER + 5), missed=["Кто-то"]))


def test_a_player_out_of_names_is_not_asked_for_another():
    spent = Game(turns=[turn()] * 20, missed=["Раз", "Два", "Три"])
    assert spent.out_of_names is True
    assert guess.must_name(spent) is False


def test_the_party_has_an_end():
    assert Game(turns=[turn()] * guess.MAX_QUESTIONS).out_of_questions is True
    assert Game(turns=[turn()] * (guess.MAX_QUESTIONS - 1)).left == 1


# --- разбор ответа модели ---------------------------------------------------


def test_a_plain_object_is_read():
    step = guess.parse('{"question": "Он жив?", "reading": "Солнце светит — да"}')
    assert step.question == "Он жив?"
    assert step.reading == "Солнце светит — да"
    assert step.naming is False


def test_a_fenced_object_is_read_too():
    step = guess.parse('```json\n{"names": ["Пушкин"], "reading": "Звезда зовёт имя"}\n```')
    assert step.naming is True
    assert step.names == ["Пушкин"]


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
    assert "Пора назвать одного" in guess.prompt(game, "Звезда", CARD_YES)


def test_the_prompt_lists_the_names_already_missed():
    game = Game(turns=[turn(verdict=guess.YES)], missed=["Цой", "Высоцкий"])
    assert "Цой, Высоцкий" in guess.prompt(game, "Луна", CARD_YES)


# --- переход состояния ------------------------------------------------------


def naming_turn(*names):
    """Ход, где карты называют одного или показывают направление."""
    return Turn(
        names=list(names or ["Фредди Меркьюри"]),
        card="major_17",
        card_answer=CARD_YES,
        reading="Звезда показывает лицо",
    )


def test_a_right_name_ends_the_party():
    game = Game(turns=[naming_turn()])
    guess.grade(game, guess.YES)
    assert game.status == guess.WON
    assert game.missed == []


def test_a_wrong_name_is_remembered_and_the_party_goes_on():
    game = Game(turns=[naming_turn("Цой")])
    guess.grade(game, guess.NO)
    assert game.status == guess.ACTIVE
    assert game.missed == ["Цой"]


def test_a_missed_name_is_not_a_fact_about_the_person():
    game = Game(turns=[naming_turn("Цой"), turn("Он музыкант?", CARD_YES, guess.YES)])
    game.turns[0].verdict = guess.NO
    assert guess.facts(game) == ["Он музыкант? — да"]


def test_a_name_takes_only_yes_or_no():
    for soft in (guess.MAYBE, guess.UNKNOWN):
        game = Game(turns=[naming_turn()])
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
    game = Game(turns=[naming_turn("Три")], missed=["Раз", "Два"])
    guess.grade(game, guess.NO)
    assert game.missed == ["Раз", "Два", "Три"]
    assert game.status == guess.LOST


def test_a_rejected_name_does_not_start_a_follow_up():
    """Уточнять нечего: промах по имени — не ось, которую можно дожать."""
    game = Game(turns=[naming_turn()])
    guess.grade(game, guess.NO)
    assert guess.needs_follow_up(game) is False


# --- направление: карты показывают типаж, а не одного ----------------------


def test_a_direction_is_graded_by_the_person_not_flipped_by_the_card():
    """Вопрос поверяют картой, направление — собой. Перепутать это — провалить игру."""
    lead = naming_turn("Леонардо Ди Каприо", "Мэтт Дэймон")
    lead.card_answer = CARD_NO  # карта легла «нет», но к направлению это не относится
    lead.verdict = guess.YES
    assert guess.truth(lead) is True

    wrong = naming_turn("Пушкин", "Лермонтов")
    wrong.card_answer = CARD_YES
    wrong.verdict = guess.NO
    assert guess.truth(wrong) is False


def test_a_direction_narrows_the_circle_like_a_question_does():
    game = Game(turns=[naming_turn("Леонардо Ди Каприо", "Мэтт Дэймон")])
    guess.grade(game, guess.YES)

    assert guess.facts(game) == [
        "типаж вроде «Леонардо Ди Каприо или Мэтт Дэймон» — в нужном русле"
    ]
    # Направление мимо — не промах по имени: его в список названных не пишут
    assert game.missed == []
    assert game.status == guess.ACTIVE


def test_a_rejected_direction_cuts_off_a_whole_type():
    game = Game(turns=[naming_turn("Пушкин", "Лермонтов")])
    guess.grade(game, guess.NO)
    assert guess.facts(game) == ["типаж вроде «Пушкин или Лермонтов» — не те"]
    assert game.missed == []


def test_a_direction_never_wins_the_party():
    """Подтверждённое русло — это ещё не имя: закрыть партию может только один."""
    game = Game(turns=[naming_turn("Ди Каприо", "Мэтт Дэймон")])
    guess.grade(game, guess.YES)
    assert game.status == guess.ACTIVE

    game = Game(turns=[naming_turn("Ди Каприо")])
    guess.grade(game, guess.YES)
    assert game.status == guess.WON


def test_several_names_are_read_and_trimmed():
    step = guess.parse('{"names": ["А", "Б", "В", "Г", "Д"], "reading": "Карты показывают"}')
    assert step.lead is True
    assert step.names == ["А", "Б", "В", "Г", "Д"][: guess.MAX_NAMES]


def test_a_single_name_sent_as_a_string_is_still_a_name():
    step = guess.parse('{"names": "Пушкин", "reading": "Звезда зовёт"}')
    assert step.naming is True
    assert step.names == ["Пушкин"]


def test_the_names_line_reads_like_speech():
    assert naming_turn("Ди Каприо", "Мэтт Дэймон").names_line == "Ди Каприо или Мэтт Дэймон"
    assert naming_turn("А", "Б", "В").names_line == "А, Б или В"
    assert naming_turn("Один").names_line == "Один"


# --- очередь ходов ----------------------------------------------------------


def questions(count, verdict=guess.YES):
    return [turn(verdict=verdict) for _ in range(count)]


def test_the_first_turns_are_questions_not_hunches():
    """Каждый ход гадать, кто это, — допрос, а не расклад."""
    for asked in range(guess.LEAD_EVERY):
        assert guess.mode(Game(turns=questions(asked))) == guess.QUESTION


def test_the_type_is_shown_about_every_fifth_turn():
    assert guess.mode(Game(turns=questions(guess.LEAD_EVERY))) == guess.LEAD


def test_the_count_starts_over_from_the_last_type_shown():
    """Считаем от показанного типажа, а не по остатку от деления."""
    game = Game(turns=questions(guess.LEAD_EVERY) + [naming_turn("А", "Б")])
    game.turns[-1].verdict = guess.NO
    assert guess.mode(game) == guess.QUESTION

    game.turns.extend(questions(guess.LEAD_EVERY))
    assert guess.mode(game) == guess.LEAD


def test_pressing_a_rejected_card_outranks_the_queue():
    """Тема осталась открытой — её дожимают, даже если подошла очередь типажа."""
    game = Game(turns=questions(guess.LEAD_EVERY - 1) + [turn(verdict=guess.NO)])
    assert guess.since_lead(game) >= guess.LEAD_EVERY
    assert guess.mode(game) == guess.QUESTION


def test_the_name_outranks_the_type():
    game = Game(turns=questions(guess.GUESS_AFTER))
    assert guess.mode(game) == guess.NAME


def test_the_prompt_forbids_names_when_a_question_is_due():
    text = guess.prompt(Game(), "Шут", CARD_YES)
    assert "Сейчас задай вопрос" in text
    assert "Имена не называй" in text


def test_the_prompt_asks_for_a_type_when_it_is_due():
    text = guess.prompt(Game(turns=questions(guess.LEAD_EVERY)), "Звезда", CARD_YES)
    assert "показать направление" in text


def test_an_out_of_turn_answer_is_recognised():
    question = guess.Step(question="Он жив?")
    lead = guess.Step(names=["А", "Б"])
    name = guess.Step(names=["А"])

    assert guess.matches(question, guess.QUESTION) is True
    assert guess.matches(lead, guess.QUESTION) is False
    assert guess.matches(lead, guess.LEAD) is True
    assert guess.matches(name, guess.LEAD) is False
    assert guess.matches(name, guess.NAME) is True
    assert guess.matches(question, guess.NAME) is False


# --- хранение ---------------------------------------------------------------


def test_a_party_survives_a_round_trip_through_storage():
    game = Game(
        turns=[
            turn("Он жив?", CARD_NO, guess.NO),
            naming_turn("Ди Каприо", "Мэтт Дэймон"),
        ],
        missed=["Цой"],
        secret=None,
    )
    game.turns[1].verdict = guess.YES
    game.turns[1].reversed = True

    back = guess.game_of(guess.game_json(game))

    assert guess.facts(back) == guess.facts(game)
    assert back.missed == ["Цой"]
    assert back.turns[1].names == ["Ди Каприо", "Мэтт Дэймон"]
    assert back.turns[1].reversed is True
    assert back.turns[1].verdict == guess.YES


def test_an_empty_row_reads_as_a_fresh_party():
    for empty in (None, {}):
        game = guess.game_of(empty)
        assert game.turns == []
        assert game.status == guess.ACTIVE


# --- что можно нажать и кому можно играть -----------------------------------


def test_a_plain_question_takes_all_four_answers():
    assert guess.options(turn()) == guess.VERDICTS


def test_a_follow_up_and_names_take_only_two():
    assert guess.options(turn(follow_up=True)) == guess.BINARY
    assert guess.options(naming_turn("А", "Б")) == guess.BINARY
    assert guess.options(naming_turn("Один")) == guess.BINARY


def test_the_first_party_is_free_and_the_next_one_is_not():
    assert guess.may_start(played=0, free_games=1, paid=False, owner=False) is True
    assert guess.may_start(played=1, free_games=1, paid=False, owner=False) is False
    assert guess.may_start(played=1, free_games=1, paid=True, owner=False) is True


def test_the_owner_plays_without_paying():
    """Как и с раскладами: лимит придуман для клиентов."""
    assert guess.may_start(played=99, free_games=1, paid=False, owner=True) is True


# --- круг игрока ------------------------------------------------------------


def test_age_counts_whole_years_and_waits_for_the_birthday():
    from datetime import date

    assert guess.years(date(1975, 6, 1), date(2026, 10, 28)) == 51
    assert guess.years(date(1975, 12, 1), date(2026, 10, 28)) == 50
    assert guess.years(date(2026, 10, 28), date(2026, 10, 28)) == 0


def test_a_missing_or_absurd_birthday_gives_no_age():
    from datetime import date

    assert guess.years(None) is None
    assert guess.years(date(1700, 1, 1), date(2026, 10, 28)) is None
    assert guess.years(date(2030, 1, 1), date(2026, 10, 28)) is None


def test_the_circle_is_described_from_what_is_known():
    assert guess.audience(age=51, city="Москва") == "51 лет, живёт: Москва"
    assert guess.audience(age=51) == "51 лет"
    assert guess.audience(age=None, city="Казань") == "живёт: Казань"
    assert guess.audience(age=None, city=None) == ""


def test_the_circle_is_a_hint_and_the_prompt_says_so():
    """Пятидесятилетний из России думает о Цое, но может и о Кендрике Ламаре."""
    text = guess.prompt(Game(), "Шут", CARD_YES, about="51 лет, живёт: Москва")

    assert "51 лет, живёт: Москва" in text
    assert "из своего круга" in text
    # Главное — что это не запрет: иначе бот отбросит верный ответ
    assert "иди за фактами" in text


def test_an_unknown_circle_adds_nothing_to_the_prompt():
    text = guess.prompt(Game(), "Шут", CARD_YES)
    assert "Загадывает человек" not in text
