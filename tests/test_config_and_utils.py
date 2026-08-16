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
    assert settings.grok_timeout == 60.0


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
