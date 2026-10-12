"""Уровни авторского разбора: состав, цены и подписи."""

from __future__ import annotations

import pytest

from bot.config import Settings
from bot.tiers import find_tier, followups_allowed, tier_label, tiers


def settings(**overrides) -> Settings:
    return Settings(BOT_TOKEN="t:t", _env_file=None, **overrides)


def test_three_levels_with_the_agreed_prices():
    assert [(tier.key, tier.price) for tier in tiers(settings())] == [
        ("short", 500),
        ("base", 700),
        ("full", 1000),
    ]


def test_prices_come_from_the_environment():
    """Цены меняются без правки кода — иначе каждый пересмотр был бы релизом."""
    assert [tier.price for tier in tiers(settings(AUTHOR_PRICE_BASE=800))] == [500, 800, 1000]


def test_zero_price_hides_a_level():
    keys = [tier.key for tier in tiers(settings(AUTHOR_PRICE_FULL=0))]

    assert keys == ["short", "base"]
    assert find_tier(settings(AUTHOR_PRICE_FULL=0), "full") is None


def test_every_level_says_what_is_included():
    for tier in tiers(settings()):
        assert tier.includes
        assert tier.summary


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("short", "Лаконичный · 500 ₽"),
        ("full", "Полный · 1000 ₽"),
        (None, "не выбран"),
        ("выдуманный", "не выбран"),
    ],
)
def test_label_carries_the_price(key, expected):
    assert tier_label(settings(), key) == expected


def test_currency_is_configurable():
    assert tier_label(settings(AUTHOR_CURRENCY="RUB"), "base") == "Базовый · 700 RUB"


def test_each_level_promises_its_own_number_of_followups():
    assert [(tier.key, tier.followups) for tier in tiers(settings())] == [
        ("short", 0),
        ("base", 1),
        ("full", 3),
    ]


@pytest.mark.parametrize(("key", "expected"), [("short", 0), ("base", 1), ("full", 3)])
def test_allowance_matches_the_level(key, expected):
    assert followups_allowed(settings(), key) == expected


def test_orders_placed_before_levels_existed_keep_one_followup():
    """Старым заявкам уточнения обещали без числа — отнимать их задним числом нечестно."""
    assert followups_allowed(settings(), None) == 1


def test_promise_text_matches_the_enforced_number():
    """Текст и лимит расходиться не должны: это и был исходный дефект."""
    by_key = {tier.key: tier for tier in tiers(settings())}

    assert "без уточняющих вопросов" in " ".join(by_key["short"].includes)
    assert "один уточняющий вопрос" in " ".join(by_key["base"].includes)
    assert "до трёх уточняющих вопросов" in " ".join(by_key["full"].includes)
