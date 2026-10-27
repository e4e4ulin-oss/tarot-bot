"""Игра «карты угадывают»: правила партии, без сети и без базы.

Акинатор наоборот. Там вопросы задаёт машина, а отвечает человек; здесь
отвечают карты, а человек их проверяет. Бот спрашивает «этот человек жив?»,
тянет карту, читает её как ответ — и ждёт, прав ли он.

Почему партия сходится, хотя карты случайны: знание несёт не карта, а оценка
человека. Карта сказала «жив», человек ответил «нет» — значит человек мёртв.
Карта угадала, человек ответил «да» — то же самое с другой стороны. Бит за
ход в любом случае, а карты решают только, выглядит ли бот ясновидящим.

«Возможно» и «не знаю» бит не дают: на них бот меняет ось вопроса, а не
доспрашивает ту же.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from random import Random

from .tarot.draw import DrawnCard, card_polarity, draw_spread
from .tarot.spreads import SPREADS

# Сколько вопросов отпущено на партию. Акинатор укладывается в двадцать с
# небольшим, и дальше игра перестаёт быть игрой: человеку надоедает раньше,
# чем боту.
MAX_QUESTIONS = 25

# После стольких вопросов модель обязана назвать имя, даже если не уверена.
# Промолчать до самого конца — худший исход: партия без догадки не состоялась.
GUESS_AFTER = 12

# Сколько имён разрешено назвать мимо, прежде чем признать поражение
MAX_GUESSES = 3

YES = "yes"
NO = "no"
MAYBE = "maybe"
UNKNOWN = "unknown"
VERDICTS = (YES, NO, MAYBE, UNKNOWN)

# Что человек нажимает на уточняющем вопросе: там выбора меньше
BINARY = (YES, NO)

VERDICT_LABELS = {
    YES: "Да",
    NO: "Нет",
    MAYBE: "Возможно",
    UNKNOWN: "Не знаю",
}

# Ответ карты. Неопределённого здесь нет: карту с нулевой полярностью
# перетягивают, иначе ход прошёл бы впустую
CARD_YES = "да"
CARD_NO = "нет"

ACTIVE = "active"
WON = "won"
LOST = "lost"


def decisive(
    *,
    allow_reversed: bool = True,
    reversed_chance: float = 0.4,
    rng: Random | None = None,
    attempts: int = 12,
) -> tuple[DrawnCard, str]:
    """Тянет карту, которая отвечает «да» или «нет».

    У части карт полярность нулевая — в раскладе это честное «ответ пока не
    определён», но в игре такой ход прошёл бы впустую: ни карта не ответила,
    ни человеку нечего оценивать. Поэтому неопределившуюся карту перетягивают.
    """
    card = None
    for _ in range(attempts):
        card = draw_spread(
            SPREADS["yes_no"],
            allow_reversed=allow_reversed,
            reversed_chance=reversed_chance,
            rng=rng,
        )[0]
        polarity = card_polarity(card.card)
        if card.reversed:
            polarity = -polarity
        if polarity > 0:
            return card, CARD_YES
        if polarity < 0:
            return card, CARD_NO

    # Столько нейтральных подряд — случай почти невозможный, но ход всё равно
    # должен состояться: решает положение карты
    assert card is not None
    return card, CARD_NO if card.reversed else CARD_YES


@dataclass
class Turn:
    """Один ход: вопрос или имя, карта как ответ и оценка человека.

    Догадка — такой же ход: карты называют имя, человек говорит, попали ли.
    Отдельной сущностью она была бы только лишним швом.
    """

    question: str
    card: str  # идентификатор карты, как в колоде: major_09, cups_03
    card_answer: str  # CARD_YES или CARD_NO
    reading: str  # как модель прочитала эту карту под этот вопрос
    follow_up: bool = False
    verdict: str | None = None  # пока не оценён — None
    guess: str = ""  # если ход — попытка назвать имя
    reversed: bool = False

    @property
    def graded(self) -> bool:
        return self.verdict is not None

    @property
    def is_guess(self) -> bool:
        return bool(self.guess)


@dataclass
class Game:
    """Партия целиком. Хранится как есть: ходы читаются только вместе."""

    turns: list[Turn] = field(default_factory=list)
    guesses: list[str] = field(default_factory=list)
    status: str = ACTIVE
    secret: str | None = None  # кого загадали, если человек рассказал

    @property
    def asked(self) -> int:
        return len(self.turns)

    @property
    def pending(self) -> Turn | None:
        """Ход, который ждёт оценки. Больше одного такого не бывает."""
        last = self.turns[-1] if self.turns else None
        return last if last is not None and not last.graded else None

    @property
    def left(self) -> int:
        return max(0, MAX_QUESTIONS - self.asked)

    @property
    def out_of_questions(self) -> bool:
        return self.asked >= MAX_QUESTIONS

    @property
    def out_of_guesses(self) -> bool:
        return len(self.guesses) >= MAX_GUESSES


def truth(turn: Turn) -> bool | None:
    """Что на самом деле установлено этим ходом.

    Оценка человека относится к ответу карты, а не к вопросу: «нет» означает
    «карта ошиблась», то есть правда — противоположность сказанного.
    """
    if turn.verdict == YES:
        return turn.card_answer == CARD_YES
    if turn.verdict == NO:
        return turn.card_answer != CARD_YES
    return None  # «возможно» и «не знаю» ничего не устанавливают


def facts(game: Game) -> list[str]:
    """Установленное за партию — то, на чём модель строит следующий вопрос."""
    lines = []
    for turn in game.turns:
        if turn.is_guess:
            continue  # промах по имени — не факт о человеке, он в guesses
        answer = truth(turn)
        if answer is None:
            if turn.graded:
                lines.append(f"{turn.question} — человек не знает")
            continue
        lines.append(f"{turn.question} — {'да' if answer else 'нет'}")
    return lines


def grade(game: Game, verdict: str) -> Game:
    """Применяет оценку человека к ждущему ходу и двигает партию.

    Единственное место, где меняется состояние: чтобы не оказалось двух
    вариантов правды — в API одна, в чате другая.
    """
    turn = game.pending
    if turn is None:
        raise ValueError("нечего оценивать: все ходы уже оценены")
    if verdict not in VERDICTS:
        raise ValueError(f"неизвестная оценка: {verdict}")
    if turn.is_guess and verdict not in BINARY:
        # На имя отвечают только «да» или «нет»: «возможно» тут бессмысленно
        raise ValueError("на догадку отвечают да или нет")
    if turn.follow_up and verdict not in BINARY:
        raise ValueError("на уточняющий вопрос отвечают да или нет")

    turn.verdict = verdict

    if turn.is_guess:
        if verdict == YES:
            game.status = WON
            return game
        game.guesses.append(turn.guess)

    if game.out_of_questions or game.out_of_guesses:
        # Бюджет кончился: бот сдаётся и спрашивает, кого загадали
        game.status = LOST
    return game


def needs_follow_up(game: Game) -> bool:
    """Следующий вопрос — уточняющий по той же оси.

    Так просил владелец: карта ошиблась — значит тему не закрыли, а только
    развернули, и её стоит дожать одним бинарным вопросом.
    """
    graded = [turn for turn in game.turns if turn.graded and not turn.is_guess]
    if not graded:
        return False
    last = graded[-1]
    # Уточнение к уточнению не делаем: иначе игра застрянет на одной оси
    return last.verdict == NO and not last.follow_up


def must_guess(game: Game) -> bool:
    """Пора назвать имя, даже если уверенности нет.

    Не на каждом ходу после порога, иначе бот начнёт сыпать именами подряд:
    первая догадка на двенадцатом вопросе, следующая — через пять.
    """
    if game.out_of_guesses:
        return False
    return game.asked >= GUESS_AFTER + 5 * len(game.guesses)


def finished(game: Game) -> bool:
    return game.status != ACTIVE


@dataclass(frozen=True)
class Step:
    """Что модель решила сделать на этом ходу."""

    question: str = ""
    guess: str = ""
    reading: str = ""

    @property
    def guessing(self) -> bool:
        return bool(self.guess)


_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def parse(raw: str) -> Step | None:
    """Разбирает ответ модели. Не разобрался — пусть решает вызывающий.

    Модели любят обернуть JSON в рассуждение или в тройные кавычки, и
    падать из-за этого посреди партии незачем.
    """
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    question = str(data.get("question") or "").strip()
    guess = str(data.get("guess") or "").strip()
    reading = str(data.get("reading") or "").strip()
    if not question and not guess:
        return None
    return Step(question=question, guess=guess, reading=reading)


SYSTEM = (
    "Ты ведёшь игру: человек загадал известную личность — живую, умершую или "
    "вымышленного персонажа, — а ты угадываешь её по картам таро.\n\n"
    "Правила, которые нельзя нарушать:\n"
    "1. Ты задаёшь ровно один вопрос за ход. Вопрос бинарный: на него есть "
    "ответ «да» или «нет», и никакой другой.\n"
    "2. Ответ на вопрос даёт выпавшая карта, а не ты. Тебе её называют вместе "
    "с ответом, и ты обязан прочитать карту именно так, как она ответила, "
    "даже если считаешь иначе.\n"
    "3. Человек потом скажет, права ли карта. Его оценка — и есть правда.\n"
    "4. Спрашивай о том, что делит оставшихся кандидатов примерно пополам: "
    "эпоха, страна, род занятий, пол, слава при жизни. Не спрашивай о мелочах, "
    "пока не отсечена большая часть.\n"
    "5. Не повторяй вопрос, ответ на который уже установлен.\n\n"
    "Отвечай только объектом JSON, без пояснений вокруг:\n"
    '{"question": "вопрос", "reading": "одна фраза: как эта карта отвечает"}\n'
    "или, когда готов назвать имя:\n"
    '{"guess": "Имя", "reading": "одна фраза: что сказала карта"}\n\n'
    "reading — одно предложение по-русски, от лица карт, без оговорок вроде "
    "«возможно» и без упоминания правил игры."
)


def prompt(game: Game, card: str, card_answer: str) -> str:
    """Что модель видит перед ходом."""
    parts = []

    known = facts(game)
    if known:
        parts.append("Установлено:\n" + "\n".join(f"— {line}" for line in known))
    else:
        parts.append("Пока ничего не установлено: это первый вопрос.")

    if game.guesses:
        parts.append("Уже названы мимо: " + ", ".join(game.guesses))

    parts.append(f"Выпала карта: {card}. Её ответ на твой вопрос: {card_answer}.")

    if needs_follow_up(game):
        parts.append(
            "Прошлый ответ карты человек отверг. Задай уточняющий вопрос по той "
            "же теме, чтобы дожать её, а не переходи к новой."
        )

    if must_guess(game):
        parts.append(
            f"Вопросов задано {game.asked}, осталось {game.left}. "
            "Пора назвать имя: верни guess, а не question."
        )
    else:
        parts.append(f"Вопросов задано {game.asked}, осталось {game.left}.")

    return "\n\n".join(parts)
