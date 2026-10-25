"""Проверка подписи Telegram Mini App.

Мини-приложение открывается внутри Telegram и получает от клиента строку
`initData` — те же данные, что видит бот, но подписанные токеном. Подпись
проверяется этим токеном: без неё любой мог бы прислать чужой user_id и
получить чужие расклады.

Арендаторов у нас несколько, у каждого свой бот и свой токен. Чьим токеном
подпись сошлась — тот арендатор и обслуживает запрос, поэтому отдельного
параметра «кто вы» в запросах не нужно: он выводится из самой подписи.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

# Telegram подписывает ключом, выведенным из токена с этой солью.
SECRET_SALT = b"WebAppData"

# Сколько живёт подпись. Телеграм не обновляет initData, пока приложение открыто,
# поэтому сутки — разумный предел: дольше держать окно открытым никто не станет,
# а украденная строка перестаёт работать на следующий день.
MAX_AGE = 24 * 60 * 60


class AuthError(Exception):
    """Подпись не сошлась, протухла или данных нет вовсе."""


@dataclass(frozen=True)
class WebAppUser:
    """Кто открыл приложение. Поля те же, что у телеграмного пользователя."""

    id: int
    first_name: str
    last_name: str | None
    username: str | None
    language_code: str | None
    is_premium: bool
    photo_url: str | None


@dataclass(frozen=True)
class Identity:
    """Проверенный посетитель вместе с арендатором, чьим ботом он пришёл."""

    user: WebAppUser
    tenant_id: int
    auth_date: int
    query_id: str | None
    start_param: str | None


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(SECRET_SALT, bot_token.encode(), hashlib.sha256).digest()


def _data_check_string(fields: dict[str, str]) -> str:
    """Все поля кроме подписи, отсортированные по имени, по строке на поле."""
    pairs = sorted(f"{key}={value}" for key, value in fields.items() if key != "hash")
    return "\n".join(pairs)


def signature_matches(init_data: str, bot_token: str) -> bool:
    """Подписана ли строка этим токеном. Ничего не бросает — только да или нет."""
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.get("hash")
    if not received:
        return False
    expected = hmac.new(
        _secret_key(bot_token), _data_check_string(fields).encode(), hashlib.sha256
    ).hexdigest()
    # Сравнение постоянного времени: иначе подпись можно подобрать по задержке
    return hmac.compare_digest(expected, received)


def parse_user(raw: str) -> WebAppUser:
    data = json.loads(raw)
    return WebAppUser(
        id=int(data["id"]),
        first_name=data.get("first_name") or "",
        last_name=data.get("last_name"),
        username=data.get("username"),
        language_code=data.get("language_code"),
        is_premium=bool(data.get("is_premium")),
        photo_url=data.get("photo_url"),
    )


def verify(
    init_data: str,
    tokens: dict[int, str],
    *,
    max_age: int = MAX_AGE,
    now: float | None = None,
) -> Identity:
    """Проверить подпись и вернуть посетителя с его арендатором.

    `tokens` — токены арендаторов: {tenant_id: bot_token}. Перебор дешёвый:
    арендаторов единицы, а каждая проверка — один HMAC.
    """
    if not init_data:
        raise AuthError("нет данных авторизации")

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    if "hash" not in fields:
        raise AuthError("подпись отсутствует")

    tenant_id = next(
        (tid for tid, token in tokens.items() if token and signature_matches(init_data, token)),
        None,
    )
    if tenant_id is None:
        raise AuthError("подпись не сошлась")

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise AuthError("дата подписи испорчена") from exc

    moment = time.time() if now is None else now
    if auth_date <= 0 or moment - auth_date > max_age:
        raise AuthError("подпись устарела, перезапустите приложение")

    raw_user = fields.get("user")
    if not raw_user:
        raise AuthError("в данных нет пользователя")
    try:
        user = parse_user(raw_user)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise AuthError("данные пользователя испорчены") from exc

    return Identity(
        user=user,
        tenant_id=tenant_id,
        auth_date=auth_date,
        query_id=fields.get("query_id"),
        start_param=fields.get("start_param"),
    )


def init_data_from_header(value: str | None) -> str:
    """Достаёт initData из заголовка `Authorization: tma <initData>`."""
    if not value:
        return ""
    prefix, _, rest = value.partition(" ")
    if prefix.lower() != "tma":
        return ""
    return rest.strip()
