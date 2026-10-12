"""Шаги воронки: что считаем и в каком порядке показываем.

Названия шагов живут отдельно от обработчиков, чтобы в базе не появилось
«author_menu» в одном месте и «menu_author» в другом.
"""

from __future__ import annotations

from typing import Final

START: Final = "start"
AUTO_OPEN: Final = "auto_open"
AUTO_READING: Final = "auto_reading"
AUTHOR_OPEN: Final = "author_open"
TIER_CHOSEN: Final = "tier_chosen"
ORDER_SENT: Final = "order_sent"
ORDER_ANSWERED: Final = "order_answered"

# Карта дня живёт рядом с воронкой, но в неё не входит: это не путь к заказу
DAILY_OPEN: Final = "daily_open"
DAILY_SUBSCRIBED: Final = "daily_subscribed"
DAILY_SENT: Final = "daily_sent"

# Порядок важен: по нему считаются переходы между шагами.
FUNNEL: Final[tuple[tuple[str, str], ...]] = (
    (START, "Зашли в бота"),
    (AUTO_OPEN, "Открыли авто-расклад"),
    (AUTO_READING, "Получили расклад"),
    (AUTHOR_OPEN, "Открыли авторский"),
    (TIER_CHOSEN, "Выбрали уровень"),
    (ORDER_SENT, "Отправили заявку"),
    (ORDER_ANSWERED, "Получили разбор"),
)
