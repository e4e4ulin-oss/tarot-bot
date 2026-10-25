"""Подпись мини-приложения: пускаем только своих и только сегодня."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from bot.api.auth import AuthError, init_data_from_header, signature_matches, verify

TOKEN = "123456:AAHtest-token-for-signature-checks"
OTHER_TOKEN = "654321:BBHanother-token-entirely-different"


def make_init_data(token: str, *, user_id: int = 42, auth_date: int | None = None) -> str:
    """Собирает строку ровно так, как её собирает клиент Telegram."""
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAEtest",
        "user": json.dumps(
            {"id": user_id, "first_name": "Диана", "username": "diana"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    check = "\n".join(sorted(f"{k}={v}" for k, v in fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_signature_of_own_token_matches():
    assert signature_matches(make_init_data(TOKEN), TOKEN)


def test_signature_of_other_token_does_not_match():
    assert not signature_matches(make_init_data(OTHER_TOKEN), TOKEN)


def test_verify_returns_user_and_tenant():
    identity = verify(make_init_data(OTHER_TOKEN, user_id=7), {1: TOKEN, 2: OTHER_TOKEN})
    assert identity.user.id == 7
    assert identity.user.first_name == "Диана"
    # Арендатор выведен из того, чьим токеном подписано
    assert identity.tenant_id == 2


def test_verify_rejects_foreign_signature():
    with pytest.raises(AuthError):
        verify(make_init_data(OTHER_TOKEN), {1: TOKEN})


def test_verify_rejects_tampered_user():
    data = make_init_data(TOKEN, user_id=42)
    spoofed = data.replace("%2242%22", "%2243%22").replace("42", "43", 1)
    with pytest.raises(AuthError):
        verify(spoofed, {1: TOKEN})


def test_verify_rejects_stale_signature():
    old = int(time.time()) - 48 * 60 * 60
    with pytest.raises(AuthError):
        verify(make_init_data(TOKEN, auth_date=old), {1: TOKEN})


def test_verify_rejects_empty():
    with pytest.raises(AuthError):
        verify("", {1: TOKEN})


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("tma abc", "abc"),
        ("tma  abc  ", "abc"),
        ("TMA abc", "abc"),
        ("Bearer abc", ""),
        (None, ""),
        ("", ""),
    ],
)
def test_init_data_from_header(header, expected):
    assert init_data_from_header(header) == expected
