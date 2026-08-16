from datetime import UTC, datetime

from bot.config import Settings
from bot.texts import short, topic_label
from bot.utils import split_text


def test_admin_ids_parsed_from_string():
    settings = Settings(BOT_TOKEN="t", ADMIN_IDS="10, 20;30")
    assert settings.admin_ids == [10, 20, 30]
    assert settings.is_admin(20)
    assert not settings.is_admin(40)


def test_empty_values_fall_back_to_defaults():
    """Пустые строки в .env не должны ронять бота на старте."""
    settings = Settings(
        BOT_TOKEN="t",
        ADMIN_CHAT_ID="",
        ADMIN_IDS="",
        DAILY_AUTO_LIMIT="",
        REVERSED_CHANCE="",
        GROK_TIMEOUT="",
    )
    assert settings.admin_chat_id == 0
    assert settings.admin_ids == []
    assert settings.daily_auto_limit == 5
    assert settings.reversed_chance == 0.4
    assert settings.grok_timeout == 30.0
    assert settings.grok_deadline == 45.0


def test_ai_enabled_depends_on_key():
    assert not Settings(BOT_TOKEN="t", XAI_API_KEY="").ai_enabled
    assert Settings(BOT_TOKEN="t", XAI_API_KEY="k").ai_enabled


def test_split_text_keeps_chunks_within_limit():
    text = "\n\n".join("абзац " * 30 for _ in range(20))
    chunks = split_text(text, limit=500)
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_text_handles_single_long_paragraph():
    chunks = split_text("x" * 1200, limit=500)
    assert len(chunks) == 3
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_split_text_short_text_untouched():
    assert split_text("коротко") == ["коротко"]


def test_short_truncates_with_ellipsis():
    assert short("а" * 200, 50).endswith("…")
    assert len(short("а" * 200, 50)) == 50
    assert short("коротко", 50) == "коротко"


def test_topic_label_fallback():
    assert topic_label("love") == "Отношения"
    assert topic_label(None) == "не указана"
    assert topic_label("unknown") == "unknown"


def test_fmt_date():
    from bot.utils import fmt_date

    value = datetime(2026, 8, 16, 12, 30, tzinfo=UTC)
    assert fmt_date(value) == "16.08.2026 12:30"
    assert fmt_date(None) == "—"


def test_missing_bot_token_gives_readable_message(monkeypatch):
    """Вместо трассировки pydantic пользователь должен видеть понятную подсказку."""
    from pydantic import ValidationError

    from bot.config import Settings, describe_missing_settings

    monkeypatch.delenv("BOT_TOKEN", raising=False)
    try:
        Settings(_env_file=None)
    except ValidationError as exc:
        message = describe_missing_settings(exc)
    else:  # pragma: no cover - без токена настройки собраться не должны
        raise AssertionError("ожидалась ошибка валидации")

    assert "BOT_TOKEN" in message
    assert "Variables" in message
    assert "Traceback" not in message


def test_ai_provider_accepts_both_env_names():
    """Настройки читаются и под нейтральными именами, и под старыми XAI_*/GROK_*."""
    neutral = Settings(
        BOT_TOKEN="t",
        AI_API_KEY="k1",
        AI_BASE_URL="https://api.groq.com/openai/v1",
        AI_MODEL="llama-3.3-70b-versatile",
    )
    assert neutral.ai_enabled
    assert neutral.ai_base_url.endswith("/openai/v1")
    assert neutral.ai_model == "llama-3.3-70b-versatile"

    legacy = Settings(BOT_TOKEN="t", XAI_API_KEY="k2", GROK_MODEL="grok-4")
    assert legacy.ai_api_key == "k2"
    assert legacy.ai_model == "grok-4"
    assert legacy.ai_base_url == "https://api.x.ai/v1"
