"""Конфигурация бота. Все значения читаются из переменных окружения или .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, ValidationInfo, field_validator
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
    author_name: str = Field(default="Анастасия", alias="AUTHOR_NAME")
    author_price_text: str = Field(default="", alias="AUTHOR_PRICE_TEXT")

    # --- Grok (xAI) ---
    xai_api_key: str = Field(default="", alias="XAI_API_KEY")
    xai_base_url: str = Field(default="https://api.x.ai/v1", alias="XAI_BASE_URL")
    grok_model: str = Field(default="grok-4", alias="GROK_MODEL")
    grok_timeout: float = Field(default=60.0, alias="GROK_TIMEOUT")
    grok_max_tokens: int = Field(default=1200, alias="GROK_MAX_TOKENS")
    grok_temperature: float = Field(default=0.8, alias="GROK_TEMPERATURE")

    # --- Хранилище ---
    database_url: str = Field(default="sqlite+aiosqlite:///data/tarot.db", alias="DATABASE_URL")

    # --- Правила работы ---
    daily_auto_limit: int = Field(default=5, alias="DAILY_AUTO_LIMIT")
    allow_reversed: bool = Field(default=True, alias="ALLOW_REVERSED")
    reversed_chance: float = Field(default=0.4, alias="REVERSED_CHANCE")
    max_question_length: int = Field(default=500, alias="MAX_QUESTION_LENGTH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator(
        "admin_chat_id",
        "allow_reversed",
        "daily_auto_limit",
        "grok_max_tokens",
        "grok_temperature",
        "grok_timeout",
        "max_question_length",
        "reversed_chance",
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
    def ai_enabled(self) -> bool:
        return bool(self.xai_api_key)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
