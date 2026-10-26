"""Конфигурация бота. Все значения читаются из переменных окружения или .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str = Field(alias="BOT_TOKEN")
    admin_chat_id: int = Field(default=0, alias="ADMIN_CHAT_ID")
    # NoDecode: значение приходит строкой «111,222», а не JSON-списком
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list, alias="ADMIN_IDS")

    # --- Автор ---
    # Как бот называет того, кто делает авторские разборы: имя или просто «таролог»
    author_name: str = Field(default="таролог", alias="AUTHOR_NAME")
    author_price_text: str = Field(default="", alias="AUTHOR_PRICE_TEXT")
    # Цены уровней авторского разбора. 0 — уровень не предлагается клиенту.
    author_price_short: int = Field(default=500, alias="AUTHOR_PRICE_SHORT")
    author_price_base: int = Field(default=700, alias="AUTHOR_PRICE_BASE")
    author_price_full: int = Field(default=1000, alias="AUTHOR_PRICE_FULL")
    author_currency: str = Field(default="₽", alias="AUTHOR_CURRENCY")

    # --- Доступ к авто-раскладам ---
    # Сколько раскладов человек получает бесплатно за всё время. 0 — платно сразу.
    free_auto_readings: int = Field(default=3, alias="FREE_AUTO_READINGS")
    # Бесплатные расклады даются подписанным на карту дня: подписка тоже бесплатна
    free_needs_daily: bool = Field(default=True, alias="FREE_NEEDS_DAILY")
    # Цена одного расклада после того, как бесплатные кончились
    auto_reading_price: int = Field(default=369, alias="AUTO_READING_PRICE")
    # Подробный разбор натальной карты: пишется один раз и надолго,
    # поэтому и стоит иначе, чем расклад
    natal_reading_price: int = Field(default=990, alias="NATAL_READING_PRICE")
    # Токен платёжного провайдера от BotFather. Пусто — оплата не предлагается,
    # и бот честно говорит, что платные расклады пока не подключены.
    payment_provider_token: str = Field(default="", alias="PAYMENT_PROVIDER_TOKEN")
    payment_currency: str = Field(default="RUB", alias="PAYMENT_CURRENCY")

    # --- Мини-приложение ---
    # Адрес опубликованной колоды. По умолчанию — страница, которую собирает
    # сам этот репозиторий: отдельной настройки на сервере она не требует.
    # Пусто — команда /deck отвечает, что колоды нет.
    miniapp_url: str = Field(
        default="https://e4e4ulin-oss.github.io/tarot-bot/", alias="MINIAPP_URL"
    )

    # Свой сервер для мини-приложения. Наружу его выставляет nginx: он же
    # отдаёт статику и проксирует /api, поэтому слушаем только localhost.
    # Порт по умолчанию 8082: 8081 на этом сервере занят другой службой.
    web_enabled: bool = Field(default=False, alias="WEB_ENABLED")
    web_host: str = Field(default="127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(default=8082, alias="WEB_PORT")
    # Разработка без Telegram: подписи взять неоткуда, поэтому запросы с
    # localhost считаются этим пользователем. На сервере не задаётся — без
    # номера обход не включается, и подпись проверяется как обычно.
    web_dev_user: int = Field(default=0, alias="WEB_DEV_USER")

    # --- Языковая модель ---
    # Любой OpenAI-совместимый провайдер: xAI, Groq, Google Gemini, OpenRouter, Cerebras.
    # Имена XAI_*/GROK_* оставлены ради совместимости с уже настроенными сервисами.
    ai_api_key: str = Field(default="", validation_alias=AliasChoices("AI_API_KEY", "XAI_API_KEY"))
    ai_base_url: str = Field(
        default="https://api.x.ai/v1",
        validation_alias=AliasChoices("AI_BASE_URL", "XAI_BASE_URL"),
    )
    ai_model: str = Field(default="grok-4", validation_alias=AliasChoices("AI_MODEL", "GROK_MODEL"))
    grok_timeout: float = Field(default=30.0, alias="GROK_TIMEOUT")
    # Общий предел ожидания: сколько бот вообще готов ждать разбор, включая повторы
    grok_deadline: float = Field(default=45.0, alias="GROK_DEADLINE")
    grok_retries: int = Field(default=1, alias="GROK_RETRIES")
    # Рассуждающие модели (gpt-oss, Gemini 2.5, Qwen) часть лимита тратят на
    # размышления: при 1200 разбор обрывается на полуслове
    grok_max_tokens: int = Field(default=3000, alias="GROK_MAX_TOKENS")
    grok_temperature: float = Field(default=0.8, alias="GROK_TEMPERATURE")

    # --- Карта дня ---
    daily_card_enabled: bool = Field(default=True, alias="DAILY_CARD_ENABLED")
    # Час утренней рассылки в рабочем часовом поясе
    daily_card_hour: int = Field(default=9, alias="DAILY_CARD_HOUR")
    # Сколько сообщений отправлять за один проход: Telegram не любит очереди в тысячи
    daily_card_batch: int = Field(default=25, alias="DAILY_CARD_BATCH")

    # --- Рабочее время ---
    # Срок ответа на авторский разбор считается только в эти часы, ПН–ПТ.
    work_start_hour: int = Field(default=9, alias="WORK_START_HOUR")
    work_end_hour: int = Field(default=18, alias="WORK_END_HOUR")
    work_timezone: str = Field(default="Europe/Moscow", alias="WORK_TIMEZONE")
    # Как часовой пояс называется в текстах для клиента
    work_timezone_label: str = Field(default="мск", alias="WORK_TIMEZONE_LABEL")
    # Какая доля срока должна уместиться в рабочее время, чтобы доработать сегодня.
    # 0.75: заявка в 16:30 со сроком 2 часа берётся в работу (до 18:30), а в 17:00 —
    # уже переносится на следующее утро.
    work_overflow_share: float = Field(default=0.75, alias="WORK_OVERFLOW_SHARE")

    # --- Управление службой из Telegram (/update, /restart, /logs) ---
    # Имя службы systemd и скрипт обновления; меняются, только если сервер
    # настроен нестандартно. Права на эти команды выдаёт установщик.
    service_name: str = Field(default="tarot-bot", alias="SERVICE_NAME")
    update_script: str = Field(default="/usr/local/bin/tarot-update", alias="UPDATE_SCRIPT")

    # --- Напоминание про карту дня ---
    # Тем, кто зашёл, но не подписался: бесплатные расклады открывает подписка,
    # и человек может просто не знать об этом. Предел и пауза — чтобы
    # напоминание не стало рассылкой
    nudge_enabled: bool = Field(default=True, alias="NUDGE_ENABLED")
    nudge_hour: int = Field(default=12, alias="NUDGE_HOUR")
    nudge_after_hours: int = Field(default=20, alias="NUDGE_AFTER_HOURS")
    nudge_every_days: int = Field(default=7, alias="NUDGE_EVERY_DAYS")
    nudge_max: int = Field(default=3, alias="NUDGE_MAX")
    nudge_batch: int = Field(default=25, alias="NUDGE_BATCH")

    # --- Приглашения ---
    # Сколько бесплатных раскладов получает пригласивший, когда приглашённый
    # сделал свой первый расклад, и сколько получает сам приглашённый
    referral_bonus: int = Field(default=2, alias="REFERRAL_BONUS")
    referral_welcome: int = Field(default=1, alias="REFERRAL_WELCOME")

    # --- Хранилище ---
    database_url: str = Field(default="sqlite+aiosqlite:///data/tarot.db", alias="DATABASE_URL")

    # --- Картинки карт ---
    send_card_images: bool = Field(default=True, alias="SEND_CARD_IMAGES")
    # До этого числа карты уходят отдельными фотографиями, выше — одним листом:
    # десять фото подряд превращают чат в свалку
    image_cards_limit: int = Field(default=3, alias="IMAGE_CARDS_LIMIT")

    # --- Правила работы ---
    # Предохранитель на случай, если кто-то начнёт молотить расклады подряд.
    # По умолчанию выключен: число бесплатных раскладов и так ограничено,
    # а оплаченный расклад суточным лимитом резать нечестно.
    daily_auto_limit: int = Field(default=0, alias="DAILY_AUTO_LIMIT")
    allow_reversed: bool = Field(default=True, alias="ALLOW_REVERSED")
    reversed_chance: float = Field(default=0.4, alias="REVERSED_CHANCE")
    max_question_length: int = Field(default=500, alias="MAX_QUESTION_LENGTH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator(
        "admin_chat_id",
        "allow_reversed",
        "daily_auto_limit",
        "grok_deadline",
        "grok_max_tokens",
        "grok_retries",
        "grok_temperature",
        "grok_timeout",
        "image_cards_limit",
        "nudge_enabled",
        "nudge_hour",
        "nudge_after_hours",
        "nudge_every_days",
        "nudge_max",
        "nudge_batch",
        "referral_bonus",
        "referral_welcome",
        "max_question_length",
        "send_card_images",
        "reversed_chance",
        "web_enabled",
        "web_port",
        mode="before",
    )
    @classmethod
    def _empty_means_default(cls, value: object, info: ValidationInfo) -> object:
        """Пустая строка в .env (`DAILY_AUTO_LIMIT=`) — это «оставить как по умолчанию»."""
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].get_default(call_default_factory=True)
        return value

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        return value

    @property
    def notify_chat_ids(self) -> list[int]:
        """Кому уходят заявки: админ-чат и все, кто может на них отвечать."""
        ids: list[int] = []
        for chat_id in [self.admin_chat_id, *self.admin_ids]:
            if chat_id and chat_id not in ids:
                ids.append(chat_id)
        return ids

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


MISSING_SETTINGS_HINT = """
Бот не запущен: не заданы обязательные переменные окружения — {names}.

Где их задать:
  • на хостинге (Railway, Render) — в разделе Variables того сервиса,
    который запускает бота, после чего передеплоить его;
  • на своём сервере или компьютере — в файле .env рядом с проектом.

Токен берётся у @BotFather: /mybots → ваш бот → API Token.
"""


def describe_missing_settings(error: ValidationError) -> str:
    """Понятное сообщение вместо трассировки pydantic."""
    names = [str(item["loc"][0]).upper() for item in error.errors() if item["type"] == "missing"]
    if not names:
        return f"Настройки заданы неверно:\n{error}"
    return MISSING_SETTINGS_HINT.format(names=", ".join(names))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
