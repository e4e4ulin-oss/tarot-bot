"""Игра «карты читают задуманного»: правила партии, без сети и без базы.

Акинатор наоборот. Там вопросы задаёт машина, а отвечает человек; здесь
отвечают карты, а человек их проверяет. Карты не угадывают — они предсказывают
и постепенно сужают: спрашивают «этот человек жив?», тянут карту, читают её
как ответ — и ждут, подтвердится ли.

Ход бывает трёх видов, и разница между ними важнее, чем кажется:

— вопрос: карта отвечает да или нет, человек оценивает карту;
— направление: карты показывают типаж и называют двоих-троих похожих —
  «кто-то вроде Ди Каприо или Мэтта Дэймона», — а человек говорит, в нужном
  ли русле. Здесь человек оценивает не карту, а саму догадку;
— имя: карты называют одного, и это попытка закрыть партию.

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

# Через сколько вопросов карты показывают типаж. Каждый ход — это допрос, а
# не расклад; раз в пять вопросов накопленного хватает на осмысленную догадку,
# и человек видит, что его читают, а не анкетируют.
LEAD_EVERY = 5

# Сколько похожих имён показывают за раз. Двое-трое — это типаж; пятеро —
# уже не направление, а список, и подтверждать его бессмысленно.
MAX_NAMES = 3

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
    """Один ход: вопрос или имена, карта и оценка человека.

    Имена и вопрос — одно поле на двоих было бы короче, но они по-разному
    оцениваются: вопрос человек поверяет картой, имена — собой.
    """

    question: str = ""
    names: list[str] = field(default_factory=list)
    card: str = ""  # идентификатор карты, как в колоде: major_09, cups_03
    card_answer: str = CARD_YES  # CARD_YES или CARD_NO
    reading: str = ""  # как модель прочитала эту карту под этот ход
    follow_up: bool = False
    verdict: str | None = None  # пока не оценён — None
    reversed: bool = False

    @property
    def graded(self) -> bool:
        return self.verdict is not None

    @property
    def is_lead(self) -> bool:
        """Направление: несколько похожих имён, проверка русла."""
        return len(self.names) > 1

    @property
    def is_naming(self) -> bool:
        """Одно имя: попытка закрыть партию."""
        return len(self.names) == 1

    @property
    def names_line(self) -> str:
        if len(self.names) < 2:
            return self.names[0] if self.names else ""
        return ", ".join(self.names[:-1]) + " или " + self.names[-1]


@dataclass
class Game:
    """Партия целиком. Хранится как есть: ходы читаются только вместе."""

    turns: list[Turn] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)  # имена, названные мимо
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
    def out_of_names(self) -> bool:
        return len(self.missed) >= MAX_GUESSES


def truth(turn: Turn) -> bool | None:
    """Что на самом деле установлено этим ходом.

    У вопроса оценка относится к ответу карты, а не к самому вопросу: «нет»
    означает «карта ошиблась», то есть правда — противоположность сказанного.
    У направления и имени переворачивать нечего: человек оценивает саму
    догадку, и «да» значит «да». Перепутать эти два случая легко, и партия
    после такой путаницы продолжает идти — просто строится на перевёрнутых
    фактах и не сходится никогда.
    """
    if turn.verdict not in (YES, NO):
        return None  # «возможно» и «не знаю» ничего не устанавливают
    if turn.names:
        return turn.verdict == YES
    if turn.verdict == YES:
        return turn.card_answer == CARD_YES
    return turn.card_answer != CARD_YES


def facts(game: Game) -> list[str]:
    """Установленное за партию — то, на чём модель строит следующий вопрос."""
    lines = []
    for turn in game.turns:
        if turn.is_naming:
            continue  # промах по имени — не факт о человеке, он в missed
        answer = truth(turn)
        if turn.is_lead:
            # Подтверждённое направление сужает круг не хуже вопроса, а
            # отвергнутое — отсекает целый типаж
            if answer is not None:
                verb = "в нужном русле" if answer else "не те"
                lines.append(f"типаж вроде «{turn.names_line}» — {verb}")
            continue
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
    if turn.names and verdict not in BINARY:
        # На имена отвечают только «да» или «нет»: «возможно» тут бессмысленно
        raise ValueError("на имена отвечают да или нет")
    if turn.follow_up and verdict not in BINARY:
        raise ValueError("на уточняющий вопрос отвечают да или нет")

    turn.verdict = verdict

    if turn.is_naming:
        if verdict == YES:
            game.status = WON
            return game
        game.missed.append(turn.names[0])

    if game.out_of_questions or game.out_of_names:
        # Бюджет кончился: бот сдаётся и спрашивает, кого загадали
        game.status = LOST
    return game


def needs_follow_up(game: Game) -> bool:
    """Следующий вопрос — уточняющий по той же оси.

    Так просил владелец: карта ошиблась — значит тему не закрыли, а только
    развернули, и её стоит дожать одним бинарным вопросом.
    """
    graded = [turn for turn in game.turns if turn.graded and not turn.names]
    if not graded:
        return False
    last = graded[-1]
    # Уточнение к уточнению не делаем: иначе игра застрянет на одной оси
    return last.verdict == NO and not last.follow_up


QUESTION = "question"
LEAD = "lead"
NAME = "name"


def since_lead(game: Game) -> int:
    """Сколько ходов прошло с прошлого показанного типажа.

    Считаем от последнего, а не по остатку от деления: уточнение после
    отвергнутой карты сдвигает очередь, и типаж не должен из-за этого
    пропускаться совсем.
    """
    count = 0
    for turn in reversed(game.turns):
        if turn.is_lead:
            return count
        count += 1
    return count


def mode(game: Game) -> str:
    """Что бот обязан сделать этим ходом.

    Очередь решает движок, а не модель: оставь это ей — и она либо сыплет
    именами каждый ход, либо не называет никого до самого конца.
    """
    if needs_follow_up(game):
        # Дожать отвергнутую карту важнее очереди: тема осталась открытой
        return QUESTION
    if must_name(game):
        return NAME
    if since_lead(game) >= LEAD_EVERY:
        return LEAD
    return QUESTION


def must_name(game: Game) -> bool:
    """Пора назвать одного, даже если уверенности нет.

    Не на каждом ходу после порога, иначе бот начнёт сыпать именами подряд:
    первая попытка на двенадцатом ходу, следующая — через пять.
    """
    if game.out_of_names:
        return False
    return game.asked >= GUESS_AFTER + 5 * len(game.missed)


def finished(game: Game) -> bool:
    return game.status != ACTIVE


@dataclass(frozen=True)
class Step:
    """Что модель решила сделать на этом ходу."""

    question: str = ""
    names: tuple[str, ...] | list[str] = ()
    reading: str = ""

    @property
    def naming(self) -> bool:
        """Названо одно имя — попытка закрыть партию."""
        return len(self.names) == 1

    @property
    def lead(self) -> bool:
        """Названо несколько — проверка направления."""
        return len(self.names) > 1


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
    raw_names = data.get("names")
    if isinstance(raw_names, str):
        raw_names = [raw_names]
    names = [str(name).strip() for name in (raw_names or []) if str(name).strip()]
    reading = str(data.get("reading") or "").strip()
    if not question and not names:
        return None
    return Step(question=question, names=names[:MAX_NAMES], reading=reading)


SYSTEM = (
    "Человек загадал известную личность — живую, умершую или вымышленного "
    "персонажа. Ты читаешь его по картам таро: не угадываешь, а предсказываешь "
    "и постепенно сужаешь круг.\n\n"
    "Правила, которые нельзя нарушать:\n"
    "1. За ход ты делаешь ровно одно: задаёшь вопрос, называешь направление "
    "или называешь одного человека.\n"
    "2. Вопрос бинарный: на него есть ответ «да» или «нет», и никакой другой. "
    "Ответ даёт выпавшая карта, а не ты. Тебе её называют вместе с ответом, и "
    "ты обязан прочитать карту именно так, как она ответила, даже если "
    "считаешь иначе.\n"
    "3. Направление — это двое или трое похожих людей, которые подходят под "
    "всё установленное: «карты показывают кого-то вроде Леонардо Ди Каприо "
    "или Мэтта Дэймона». Так человек говорит, в нужном ли ты русле, не "
    "называя задуманного. Бери людей действительно похожих по типажу, эпохе и "
    "роду занятий, а не просто известных.\n"
    "4. Имя — одно, и это попытка закрыть партию.\n"
    "5. Что именно делать этим ходом, тебе говорят в конце. Очередь задаёшь "
    "не ты: вопросы идут подряд, типаж показывается примерно раз в пять "
    "ходов, имя — ближе к концу.\n"
    "6. Спрашивай о том, что делит оставшихся примерно пополам: эпоха, страна, "
    "род занятий, пол, слава при жизни. О мелочах — только когда отсечена "
    "большая часть.\n"
    "7. Не повторяй то, что уже установлено, и не называй имена из списка "
    "названных мимо.\n"
    "8. Называй только тех, кто существует и широко известен. Выдуманное имя "
    "портит партию: человеку нечего подтверждать.\n\n"
    "Отвечай только объектом JSON, без пояснений вокруг:\n"
    '{"question": "вопрос", "reading": "одна фраза: как эта карта отвечает"}\n'
    "или\n"
    '{"names": ["Имя", "Имя"], "reading": "одна фраза: кого показывают карты"}\n'
    "или, когда готов назвать одного:\n"
    '{"names": ["Имя"], "reading": "одна фраза: кого называют карты"}\n\n'
    "reading — одно предложение по-русски, от лица карт. Без оговорок вроде "
    "«возможно», без упоминания правил игры и без слова «угадал»."
)


def prompt(game: Game, card: str, card_answer: str) -> str:
    """Что модель видит перед ходом."""
    parts = []

    known = facts(game)
    if known:
        parts.append("Установлено:\n" + "\n".join(f"— {line}" for line in known))
    else:
        parts.append("Пока ничего не установлено: это первый вопрос.")

    if game.missed:
        parts.append("Уже названы мимо: " + ", ".join(game.missed))

    parts.append(f"Выпала карта: {card}. Её ответ на твой вопрос: {card_answer}.")

    if needs_follow_up(game):
        parts.append(
            "Прошлый ответ карты человек отверг. Задай уточняющий вопрос по той "
            "же теме, чтобы дожать её, а не переходи к новой."
        )

    want = mode(game)
    if want == NAME:
        order = "Пора назвать одного: верни names ровно с одним именем."
    elif want == LEAD:
        order = (
            "Пора показать направление: верни names с двумя-тремя похожими "
            "людьми, которые подходят под всё установленное. Вопрос сейчас "
            "не задавай."
        )
    else:
        order = "Сейчас задай вопрос. Имена не называй — рано."
    parts.append(f"Ходов сделано {game.asked}, осталось {game.left}. {order}")

    return "\n\n".join(parts)


def matches(step: Step, want: str) -> bool:
    """Сделала ли модель то, что от неё требовалось на этом ходу."""
    if want == NAME:
        return step.naming
    if want == LEAD:
        return step.lead
    return bool(step.question) and not step.names
