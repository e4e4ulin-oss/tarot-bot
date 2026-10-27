"""HTTP-слой мини-приложения.

Приложение заменяет бота: всё, что человек раньше делал кнопками в переписке,
он делает здесь. Поэтому API живёт в том же процессе, что и бот, и зовёт те же
модули — колоду, раскладки, толкователя, репозиторий. Дублировать логику в двух
местах значит обречь её разойтись.

Наружу сервер не смотрит: его закрывает nginx, который отдаёт статику и
проксирует всё, что начинается с /api. Поэтому слушаем только localhost.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiohttp import web

from .. import analytics, billing, geo, referrals, texts
from .. import profile as profile_parse
from .. import promo as promo_rules
from ..access import check as check_access
from ..access import owns
from ..billing import PAYLOAD_AUTO, PAYLOAD_NATAL
from ..config import Settings
from ..db import Repo
from ..db.models import ORDER_STATUS_LABELS, Order, OrderStatus, ReadingMode
from ..handlers.author import notify_admin_followup, notify_admin_new_order
from ..natal import forecast as natal_forecast
from ..natal import full as natal_full
from ..natal import timeline as natal_timeline
from ..profile import describe, stored_place
from ..schedule import now
from ..services import ServerOps
from ..services import orders as order_rules
from ..services import reading as reading_rules
from ..services.daily import card_of_the_day
from ..services.grok import GrokClient, GrokError
from ..services.interpreter import Interpreter
from ..services.server import LOGS_DEFAULT
from ..tarot import SPREAD_ORDER, SPREADS, DrawnCard, deserialize, get_spread
from ..tiers import find_tier, followups_allowed, tier_label
from ..tiers import tiers as author_tiers
from .auth import AuthError, Identity, WebAppUser, init_data_from_header, verify

logger = logging.getLogger(__name__)

# Ключи в словаре запроса: кто пришёл и чьи настройки применять
IDENTITY = web.AppKey("identity", Identity)
BRAND = web.AppKey("brand", Settings)
SESSIONS = web.AppKey("session_factory", object)
INTERPRETER = web.AppKey("interpreter", Interpreter)
PENDING = web.AppKey("pending", dict)
BOTS = web.AppKey("bots", object)
OPS = web.AppKey("ops", object)

# Сколько ждёт расклад, которому не заказали разбор. Человек видит карты и
# может закрыть приложение — держать такую запись дольше незачем.
PENDING_TTL = 15 * 60

# Число, под которое ложатся карты: жест, а не счёт
NUMBER_LIMIT = 99

# Эти адреса отвечают без подписи: по ним приложение решает, что показывать
# на экране входа, ещё не зная, кто перед ним.
PUBLIC_PATHS = frozenset({"/api/health", "/api/config"})

# Служебный адрес проверяет себя сам: подписи Telegram у присмотра снаружи нет
OPS_PREFIX = "/api/ops/"

TokensProvider = Callable[[], Awaitable[dict[int, str]]]
BrandProvider = Callable[[int], Awaitable[Settings]]
BotProvider = Callable[[int], Awaitable["Bot | None"]]


def json_ok(payload: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def json_error(message: str, status: int = 400, code: str = "error") -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


@web.middleware
async def errors_middleware(request: web.Request, handler):
    """Неожиданная ошибка не должна приезжать в приложение трейсбеком."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("ошибка в %s %s", request.method, request.path)
        return json_error("Что-то пошло не так, попробуйте ещё раз", 500, "internal")


def dev_identity(request: web.Request, settings: Settings) -> Identity | None:
    """Кто пришёл, когда Telegram не при делах.

    Нужно ровно для одного: открыть приложение в обычном браузере и увидеть
    экраны. Включается только номером в настройках и только для запросов с
    самой машины — снаружи такой запрос до сервера не доходит.
    """
    if not settings.web_dev_user:
        return None
    peer = request.remote or ""
    if peer not in ("127.0.0.1", "::1"):
        return None
    user = WebAppUser(
        id=settings.web_dev_user,
        first_name="Разработка",
        last_name=None,
        username=None,
        language_code="ru",
        is_premium=False,
        photo_url=None,
    )
    return Identity(user=user, tenant_id=1, auth_date=0, query_id=None, start_param=None)


def auth_middleware(tokens: TokensProvider, brands: BrandProvider):
    """Пускает дальше только с подписью Telegram и подкладывает арендатора."""

    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path in PUBLIC_PATHS or request.path.startswith(OPS_PREFIX):
            return await handler(request)

        init_data = init_data_from_header(request.headers.get("Authorization"))
        try:
            identity = verify(init_data, await tokens())
        except AuthError as exc:
            identity = dev_identity(request, request.app[BRAND])
            if identity is None:
                return json_error(str(exc), 401, "unauthorized")
            logger.warning("впущен без подписи как %s — режим разработки", identity.user.id)

        request[IDENTITY] = identity
        request[BRAND] = await brands(identity.tenant_id)
        return await handler(request)

    return middleware


def spread_payload(key: str) -> dict[str, Any]:
    spread = SPREADS[key]
    return {
        "key": spread.key,
        "title": spread.title,
        "subtitle": spread.subtitle,
        "positions": list(spread.positions),
        "size": spread.size,
        "yes_no": spread.yes_no,
    }


async def handle_health(request: web.Request) -> web.Response:
    return json_ok({"ok": True})


async def handle_config(request: web.Request) -> web.Response:
    """Витрина до входа: чей это бренд, какие раскладки и почём разборы."""
    settings: Settings = request.app[BRAND]
    auto = billing.terms(settings, PAYLOAD_AUTO)
    natal = billing.terms(settings, PAYLOAD_NATAL)
    return json_ok(
        {
            "author": settings.author_name,
            "currency": settings.author_currency,
            "ai": settings.ai_enabled,
            "spreads": [spread_payload(key) for key in SPREAD_ORDER],
            "tiers": [
                {
                    "key": tier.key,
                    "title": tier.title,
                    "price": tier.price,
                    "hours": tier.hours,
                    "followups": tier.followups,
                    "summary": tier.summary,
                    "includes": list(tier.includes),
                }
                for tier in author_tiers(settings)
            ],
            # Цены — в той валюте, которой сейчас платят: показывать рубли и
            # списывать звёзды было бы обманом
            "auto_price": auto.price if auto else settings.auto_reading_price,
            "natal_price": natal.price if natal else settings.natal_reading_price,
            "pay_unit": auto.unit if auto else settings.author_currency,
            "pay_method": auto.method if auto else None,
            "free_readings": settings.free_auto_readings,
            "free_needs_daily": settings.free_needs_daily,
            "free_without_daily": settings.free_without_daily,
            "free_per_day": settings.free_daily_limit,
        }
    )


async def handle_spreads(request: web.Request) -> web.Response:
    return json_ok({"spreads": [spread_payload(key) for key in SPREAD_ORDER]})


async def handle_me(request: web.Request) -> web.Response:
    """Личный кабинет: кто вы, что заполнено, сколько раскладов осталось."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        user = await repo.upsert_user(
            identity.user.id,
            identity.user.username,
            identity.user.first_name,
            subscribe_new=settings.daily_subscribe_new,
        )
        used = await repo.count_auto_readings(identity.user.id)
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        history = await repo.last_readings(identity.user.id, limit=10)
        order = await repo.open_order_for_user(identity.user.id)
        # Остаток спрашиваем у правил доступа: у человека могут быть расклады
        # за приглашённых друзей и за промокоды, и своя арифметика их потеряет
        access = await check_access(repo, settings, identity.user.id)
        left = access.free_left

    granted = access.granted
    return json_ok(
        {
            "user": {
                "id": user.id,
                "first_name": user.first_name,
                "username": user.username,
                "photo_url": identity.user.photo_url,
            },
            "birth": {
                "date": user.birth_date.isoformat() if user.birth_date else None,
                "time": user.birth_time,
                "city": user.birth_city,
            },
            "city": user.city,
            "daily_subscribed": subscribed,
            # Хозяйские экраны прячутся от клиентов, но решает всё равно сервер
            "admin": owns(settings, identity.user.id),
            "readings": {
                "used": used,
                "free_left": left,
                "free_limit": granted,
                "bonus": user.bonus_readings or 0,
                # Сколько останется, если отключить карту дня: об этом человека
                # предупреждают до отключения, а не после
                "without_daily": access.without_daily,
                "per_day": settings.free_daily_limit,
                "needs_daily": settings.free_needs_daily and not subscribed,
            },
            "history": [
                {
                    "id": item.id,
                    "mode": item.mode.value if hasattr(item.mode, "value") else str(item.mode),
                    "spread": item.spread_key,
                    "title": SPREADS[item.spread_key].title
                    if item.spread_key in SPREADS
                    else item.spread_key,
                    "question": item.question,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in history
            ],
            "open_order": None
            if order is None
            else {
                "id": order.id,
                "tier": order.tier,
                "status": order.status.value
                if hasattr(order.status, "value")
                else str(order.status),
                "created_at": order.created_at.isoformat() if order.created_at else None,
            },
        }
    )


def card_payload(drawn: DrawnCard) -> dict[str, Any]:
    """Карта для экрана: картинка лежит рядом со статикой, по тому же id."""
    return {
        "id": drawn.card.id,
        "name": drawn.card.name,
        "title": drawn.title,
        "text": drawn.meaning,
        "keywords": list(drawn.keywords),
        "reversed": drawn.reversed,
        "image": f"cards/{drawn.card.id}.jpg",
    }


async def handle_daily_get(request: web.Request) -> web.Response:
    """Что уже вытянуто сегодня. Новую карту здесь не тянем: это делает человек."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    day = now(settings).date()
    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        stored = await repo.daily_card(identity.user.id, day)
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        card = None
        if stored is not None:
            drawn = await card_of_the_day(repo, settings, identity.user.id, day)
            card = card_payload(drawn)

    return json_ok({"card": card, "subscribed": subscribed, "day": day.isoformat()})


async def handle_daily_draw(request: web.Request) -> web.Response:
    """Вытянуть карту дня. Повторный вызов за те же сутки вернёт ту же карту."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    day = now(settings).date()
    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        await repo.upsert_user(
            identity.user.id,
            identity.user.username,
            identity.user.first_name,
            subscribe_new=settings.daily_subscribe_new,
        )
        drawn = await card_of_the_day(repo, settings, identity.user.id, day)
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        await repo.log_event(identity.user.id, analytics.DAILY_OPEN)

    return json_ok({"card": card_payload(drawn), "subscribed": subscribed})


async def handle_daily_subscription(request: web.Request) -> web.Response:
    """Переключатель утренней рассылки: состояние возвращаем новое."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        # В боте пользователя заводит middleware, здесь его может ещё не быть:
        # подписаться можно первым же действием, не открыв больше ничего
        await repo.upsert_user(
            identity.user.id,
            identity.user.username,
            identity.user.first_name,
            subscribe_new=settings.daily_subscribe_new,
        )
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        await repo.set_daily_subscription(identity.user.id, not subscribed)
        if not subscribed:
            await repo.log_event(identity.user.id, analytics.DAILY_SUBSCRIBED)

    return json_ok({"subscribed": not subscribed})


async def handle_cities(request: web.Request) -> web.Response:
    """Подсказки городов. Город рождения отвечает за асцендент, тёзок надо различать."""
    query = (request.query.get("q") or "").strip()
    if len(query) < 2:
        return json_ok({"cities": []})
    found = geo.find_all(query)[:8]
    return json_ok(
        {
            "cities": [
                {
                    "id": place.geonameid,
                    "name": place.name,
                    "hint": place.hint,
                    "country": place.country,
                }
                for place in found
            ]
        }
    )


async def handle_profile_save(request: web.Request) -> web.Response:
    """Данные рождения. Всё необязательно: сколько открыли, настолько и подробнее разбор."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    try:
        payload = await request.json()
    except Exception:
        return json_error("Не разобрал запрос")

    fields: dict[str, Any] = {}
    errors: list[str] = []

    if "birth_date" in payload:
        raw = (payload.get("birth_date") or "").strip()
        if not raw:
            fields["birth_date"] = None
        else:
            parsed = profile_parse.parse_date(raw)
            if parsed is None:
                errors.append("дата рождения")
            else:
                fields["birth_date"] = parsed

    if "birth_time" in payload:
        raw = (payload.get("birth_time") or "").strip()
        if not raw:
            fields["birth_time"] = None
        else:
            parsed_time = profile_parse.parse_time(raw)
            if parsed_time is None:
                errors.append("время рождения")
            else:
                fields["birth_time"] = parsed_time

    if "city" in payload:
        fields["city"] = (payload.get("city") or "").strip() or None

    place = None
    if "birth_city" in payload:
        raw = (payload.get("birth_city") or "").strip()
        fields["birth_city"] = raw or None
        # Координаты и пояс сохраняем вместе с названием: справочник меняется,
        # а небо человека меняться не должно
        if raw:
            chosen = payload.get("place_id")
            place = geo.by_id(int(chosen)) if chosen else None
            if place is None:
                matches = geo.find_all(raw)
                place = matches[0] if len(matches) == 1 else None
        fields["birth_lat"] = place.latitude if place else None
        fields["birth_lon"] = place.longitude if place else None
        fields["birth_tz"] = place.timezone if place else None

    if errors:
        return json_error("Не понял: " + ", ".join(errors))

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        await repo.upsert_user(
            identity.user.id,
            identity.user.username,
            identity.user.first_name,
            subscribe_new=settings.daily_subscribe_new,
        )
        if fields:
            await repo.save_profile(identity.user.id, **fields)
        user = await repo.get_user(identity.user.id)

    ambiguous = bool(
        fields.get("birth_city") and place is None and len(geo.find_all(fields["birth_city"])) > 1
    )
    return json_ok(
        {
            "saved": True,
            "birth": {
                "date": user.birth_date.isoformat() if user.birth_date else None,
                "time": user.birth_time,
                "city": user.birth_city,
                "located": user.birth_lat is not None,
            },
            "city": user.city,
            "ambiguous": ambiguous,
        }
    )


async def natal_card(repo: Repo, user_id: int):
    """Натальная карта по сохранённому профилю или ничего, если даты нет."""
    user = await repo.get_user(user_id)
    if user is None or user.birth_date is None:
        return None
    return natal_full(user.birth_date, user.birth_time, user.birth_city, place=stored_place(user))


async def handle_chart(request: web.Request) -> web.Response:
    identity: Identity = request[IDENTITY]
    session_factory = request.app[SESSIONS]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        card = await natal_card(repo, identity.user.id)

    if card is None:
        return json_ok({"ready": False})
    return json_ok(
        {
            "ready": True,
            "lines": card.lines(),
            "has_houses": card.has_houses,
        }
    )


async def handle_forecast(request: web.Request) -> web.Response:
    """Транзиты сейчас. Текст к ним пишет модель — отдельным запросом."""
    identity: Identity = request[IDENTITY]
    session_factory = request.app[SESSIONS]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        card = await natal_card(repo, identity.user.id)

    if card is None:
        return json_ok({"ready": False})

    moment = datetime.now(UTC).replace(tzinfo=None)
    moving = natal_forecast(card, moment)
    return json_ok(
        {
            "ready": True,
            "quiet": not moving,
            "transits": [
                {"title": str(transit), "timing": natal_timeline(transit, card, moment)}
                for transit in moving
            ],
        }
    )


async def handle_forecast_text(request: web.Request) -> web.Response:
    identity: Identity = request[IDENTITY]
    session_factory = request.app[SESSIONS]
    interpreter: Interpreter = request.app[INTERPRETER]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        card = await natal_card(repo, identity.user.id)
        user = await repo.get_user(identity.user.id)

    if card is None:
        return json_error("Сначала заполните дату рождения", 409, "no_birth_date")

    moment = datetime.now(UTC).replace(tzinfo=None)
    moving = natal_forecast(card, moment)
    if not moving:
        return json_ok({"text": None, "quiet": True})

    body = "\n".join(f"{transit}\n   {natal_timeline(transit, card, moment)}" for transit in moving)
    try:
        written = await interpreter.forecast(
            "\n".join(card.lines(aspects_limit=6)), body, describe(user)
        )
        return json_ok({"text": written.text, "quiet": False})
    except (GrokError, TimeoutError):
        logger.warning("Прогноз без текста: модель недоступна")
        return json_ok({"text": None, "quiet": False, "unavailable": True})


async def handle_natal_get(request: web.Request) -> web.Response:
    """Подробный разбор карты: уже написанный, или условия, на которых напишем."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        card = await natal_card(repo, identity.user.id)
        written = await repo.last_natal(identity.user.id)
        paid = await repo.unspent_payment(identity.user.id, PAYLOAD_NATAL)

    if card is None or not card.has_houses:
        # Без времени и города дома не считаются, а разбор без домов — не разбор
        return json_ok({"ready": False, "reason": "needs_time"})

    if written is not None:
        return json_ok({"ready": True, "text": written.interpretation, "written": True})

    deal = billing.terms(settings, PAYLOAD_NATAL)

    return json_ok(
        {
            "ready": True,
            "written": False,
            "paid": paid is not None or settings.is_admin(identity.user.id),
            "price": deal.price if deal else settings.natal_reading_price,
            "currency": deal.unit if deal else settings.author_currency,
        }
    )


async def handle_natal_write(request: web.Request) -> web.Response:
    """Пишет разбор карты. Пишется один раз и надолго, поэтому и платный."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]
    interpreter: Interpreter = request.app[INTERPRETER]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        card = await natal_card(repo, identity.user.id)
        if card is None or not card.has_houses:
            return json_error("Нужны время и город рождения", 409, "needs_time")

        written = await repo.last_natal(identity.user.id)
        if written is not None:
            return json_ok({"text": written.interpretation, "written": True})

        paid = await repo.unspent_payment(identity.user.id, PAYLOAD_NATAL)
        if paid is None and not settings.is_admin(identity.user.id):
            return json_error("Разбор карты оплачивается отдельно", 402, "needs_payment")

        user = await repo.get_user(identity.user.id)
        try:
            result = await interpreter.natal(
                "\n".join(card.lines(aspects_limit=20, deep=True)), describe(user)
            )
        except (GrokError, TimeoutError):
            logger.warning("Разбор карты не написан: модель не ответила")
            return json_error("Модель не ответила, попробуйте позже", 503, "unavailable")

        # Списываем только теперь: текст есть и сохранён
        await repo.save_reading(
            user_id=identity.user.id,
            spread_key="natal",
            question=None,
            cards=[],
            interpretation=result.text,
            ai_used=result.ai_used,
            mode=ReadingMode.NATAL,
        )
        if paid is not None:
            await repo.spend_payment(paid)

    return json_ok({"text": result.text, "written": True})


async def handle_order_create(request: web.Request) -> web.Response:
    """Заявка живому тарологу. Одна открытая на человека — как и в боте."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]
    bots: BotProvider = request.app[BOTS]

    try:
        payload = await request.json()
    except Exception:
        return json_error("Не разобрал запрос")

    question = (payload.get("question") or "").strip()
    if not question:
        return json_error("Без вопроса заявку не отправить")
    if len(question) > settings.max_question_length:
        return json_error(f"Вопрос длиннее {settings.max_question_length} символов")

    tier_key = (payload.get("tier") or "").strip() or None
    if tier_key and find_tier(settings, tier_key) is None:
        return json_error("Нет такого уровня разбора")

    topic = (payload.get("topic") or "").strip() or None
    contact = (payload.get("contact") or "").strip() or None

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        await repo.upsert_user(
            identity.user.id,
            identity.user.username,
            identity.user.first_name,
            subscribe_new=settings.daily_subscribe_new,
        )

        existing = await repo.open_order_for_user(identity.user.id)
        if existing is not None:
            return json_ok(
                {
                    "created": False,
                    "reason": "already_open",
                    "order": {
                        "id": existing.id,
                        "created_at": existing.created_at.isoformat()
                        if existing.created_at
                        else None,
                    },
                }
            )

        order = await repo.create_order(
            user_id=identity.user.id,
            question=question,
            topic=topic,
            contact=contact,
            tier=tier_key,
        )
        await repo.log_event(identity.user.id, analytics.ORDER_SENT, order.tier)

        # Заявку должен увидеть автор, а не только база: уведомление уходит
        # тем же ботом, в котором открыто приложение
        bot = await bots(identity.tenant_id)
        if bot is not None:
            full_name = " ".join(
                part for part in (identity.user.first_name, identity.user.last_name) if part
            )
            try:
                await notify_admin_new_order(
                    bot, settings, repo, order, full_name, identity.user.username
                )
            except Exception:
                logger.exception("заявка %s создана, но автору не ушла", order.id)

    tier = find_tier(settings, order.tier)
    return json_ok(
        {
            "created": True,
            "order": {"id": order.id, "tier": order.tier},
            "hours": tier.hours if tier else None,
            "author": settings.author_name,
        }
    )


async def handle_order_cancel(request: web.Request) -> web.Response:
    """Отмена своей заявки, пока автор за неё не взялся."""
    identity: Identity = request[IDENTITY]
    session_factory = request.app[SESSIONS]

    try:
        order_id = int(request.match_info["order_id"])
    except (TypeError, ValueError):
        return json_error("Нет такой заявки", 404, "not_found")

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(order_id)
        if order is None or order.user_id != identity.user.id:
            return json_error("Нет такой заявки", 404, "not_found")
        if order.status != OrderStatus.NEW:
            return json_error("Автор уже взял заявку в работу", 409, "in_progress")
        await repo.set_order_status(order, OrderStatus.CANCELLED)

    return json_ok({"cancelled": True})


# --- Авторский разбор ----------------------------------------------------
# Заявку человек оформляет здесь, ответ таролог пишет в переписке, а читается
# он снова здесь: переписка — способ доставки, а не место, где ответ живёт.


def order_json(order: Order, settings: Settings) -> dict[str, Any]:
    allowed = followups_allowed(settings, order.tier)
    label = ORDER_STATUS_LABELS.get(order.status, "")
    return {
        "id": order.id,
        "tier": order.tier,
        "tier_title": tier_label(settings, order.tier),
        "status": order.status.value if hasattr(order.status, "value") else str(order.status),
        # Подпись одна на все окна: придумывать приложению свои слова незачем
        "label": label.split(" ", 1)[-1] if label else "",
        "question": order.question,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "answered_at": order.answered_at.isoformat() if order.answered_at else None,
        "followups": {
            "allowed": allowed,
            "used": order.followups_used or 0,
            "left": max(0, allowed - (order.followups_used or 0)),
        },
    }


async def handle_orders_list(request: web.Request) -> web.Response:
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]

    async with request.app[SESSIONS]() as session:
        orders = await Repo(session, tenant_id=identity.tenant_id).orders_of(identity.user.id)
    return json_ok({"orders": [order_json(order, settings) for order in orders]})


async def handle_order_one(request: web.Request) -> web.Response:
    """Заявка целиком: вопрос, ответ таролога и уточнения по порядку."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]

    try:
        order_id = int(request.match_info["order_id"])
    except (TypeError, ValueError):
        return json_error("Заявка не найдена", 404, "unknown")

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(order_id)
        # Чужая заявка и несуществующая отвечают одинаково
        if order is None or order.user_id != identity.user.id:
            return json_error("Заявка не найдена", 404, "unknown")
        thread = await repo.order_messages(order.id)

    return json_ok(
        {
            **order_json(order, settings),
            "thread": [
                {
                    "author": item.author,
                    "text": item.text,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in thread
            ],
        }
    )


async def handle_order_followup(request: web.Request) -> web.Response:
    """Уточняющий вопрос по отвеченной заявке."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    bots: BotProvider = request.app[BOTS]

    try:
        order_id = int(request.match_info["order_id"])
    except (TypeError, ValueError):
        return json_error("Заявка не найдена", 404, "unknown")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    question = (payload.get("text") or "").strip()
    if not question:
        return json_error("Вопрос пустой", 400, "empty")
    if len(question) > settings.max_question_length:
        return json_error(
            f"Вопрос длиннее {settings.max_question_length} символов", 400, "too_long"
        )

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(order_id)
        if order is None or order.user_id != identity.user.id:
            return json_error("Заявка не найдена", 404, "unknown")
        if order.status != OrderStatus.ANSWERED:
            # Уточнять нечего, пока нет ответа
            return json_error("Ответ по этой заявке ещё не пришёл", 409, "not_answered")
        allowed = followups_allowed(settings, order.tier)
        if (order.followups_used or 0) >= allowed:
            return json_error("Уточнения по этой заявке кончились", 409, "spent")

        bot = await bots(identity.tenant_id)
        if bot is None:
            return json_error("Бот недоступен", 503, "no_bot")
        await notify_admin_followup(
            bot,
            settings,
            order,
            question,
            identity.user.first_name,
            identity.user.username,
        )
        await repo.add_order_message(order.id, "client", question)
        await repo.count_followup(order)
        await session.refresh(order)
        return json_ok(order_json(order, settings))


async def handle_reading_one(request: web.Request) -> web.Response:
    """Сохранённый расклад целиком: карты и тот самый разбор.

    Разбор пишется один раз и остаётся: перечитать его человек вправе, а
    платить второй раз за уже написанное — нет.
    """
    identity: Identity = request[IDENTITY]

    try:
        reading_id = int(request.match_info["reading_id"])
    except (TypeError, ValueError):
        return json_error("Расклад не найден", 404, "unknown")

    async with request.app[SESSIONS]() as session:
        item = await Repo(session, tenant_id=identity.tenant_id).get_reading(reading_id)

    # Чужой расклад и несуществующий отвечают одинаково: по разнице ответов
    # чужие расклады пересчитываются
    if item is None or item.user_id != identity.user.id:
        return json_error("Расклад не найден", 404, "unknown")

    spread = SPREADS.get(item.spread_key)
    drawn = deserialize(item.cards or [])
    return json_ok(
        {
            "id": item.id,
            "mode": item.mode.value if hasattr(item.mode, "value") else str(item.mode),
            "spread": item.spread_key,
            "title": spread.title if spread else item.spread_key,
            "question": item.question,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "cards": [{**card_payload(card), "position": card.position} for card in drawn],
            "text": item.interpretation or "",
            "ai": item.ai_used,
        }
    )


async def handle_invite(request: web.Request) -> web.Response:
    """Ссылка-приглашение, счёт приглашённых и остаток бесплатных раскладов."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]
    bots: BotProvider = request.app[BOTS]

    bot = await bots(identity.tenant_id)
    if bot is None:
        # Ссылка собирается из имени бота, а его знает только сам бот
        return json_error("Бот недоступен", 503, "no_bot")
    me = await bot.me()

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        reached = await repo.invited_summary(identity.user.id)
        access = await check_access(repo, settings, identity.user.id)

    return json_ok(
        {
            "link": referrals.link_for(me.username or "", identity.user.id),
            "invited": reached["invited"],
            # Сколько пришло — половина правды: без шагов не видно, приводит
            # человек клиентов или просто раздаёт ссылку
            "reached": reached,
            "bonus": settings.referral_bonus,
            "welcome": settings.referral_welcome,
            "free_left": access.free_left,
            "unlimited": access.reason == "owner",
        }
    )


async def handle_promo(request: web.Request) -> web.Response:
    """Промокод. Отказ называется словом: «не подошёл» — не ответ."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        result = await promo_rules.redeem(repo, identity.user.id, str(payload.get("code") or ""))
        if not result.ok:
            # Те же слова, что и в переписке: один отказ — один текст
            message = texts.PROMO_REFUSED.get(result.reason, texts.PROMO_REFUSED["unknown"])
            return json_error(message, 409, result.reason)
        access = await check_access(repo, settings, identity.user.id)

    return json_ok(
        {
            "readings": result.readings,
            "bonus": result.total,
            "free_left": access.free_left,
        }
    )


# --- Хозяйские экраны ----------------------------------------------------
# Прятать кнопку мало: данные отдаёт сервер, и проверять он обязан сам.


def promo_json(promo) -> dict[str, Any]:
    return {
        "id": promo.id,
        "code": promo.code,
        "readings": promo.readings,
        "uses_limit": promo.uses_limit,
        "used": promo.used,
        "expires_at": promo.expires_at.isoformat() if promo.expires_at else None,
        "active": promo.active,
        "note": promo.note,
    }


def deny_stranger(request: web.Request) -> web.Response | None:
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    if owns(settings, identity.user.id):
        return None
    return json_error("Это хозяйский экран", 403, "forbidden")


# --- Присмотр за сервером снаружи ----------------------------------------
# Только чтение: версия, состояние службы, хвост журнала. Ни обновления, ни
# перезапуска здесь нет и быть не должно — это доступ для глаз, а не для рук.


def ops_allowed(request: web.Request) -> web.Response | None:
    """Пускает по секрету из заголовка. Без секрета в настройках адреса нет."""
    settings: Settings = request.app[BRAND]
    expected = (settings.ops_token or "").strip()
    if not expected:
        # Не «403», а «нет такого»: о выключенном адресе знать незачем
        return json_error("Не найдено", 404, "not_found")

    # Секрет принимается двумя способами: своим заголовком и обычным Bearer.
    # Хранилища секретов чаще всего умеют второе, и подгонять их под нас —
    # лишний шанс ошибиться в настройке, которую потом не видно
    given = request.headers.get("X-Ops-Token", "")
    if not given:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            given = header[len("bearer ") :].strip()

    # Сравниваем байтами: compare_digest на строках с кириллицей падает,
    # а секрет вполне может оказаться русским словом
    if not secrets.compare_digest(given.encode(), expected.encode()):
        return json_error("Не найдено", 404, "not_found")
    return None


async def handle_ops_status(request: web.Request) -> web.Response:
    """Что выложено, жива ли служба и отвечает ли база."""
    if (refusal := ops_allowed(request)) is not None:
        return refusal

    ops: ServerOps = request.app[OPS]
    version = await ops.version()
    state = await ops.state()

    async with request.app[SESSIONS]() as session:
        counts = await Repo(session).stats()

    # systemctl отдаёт строки вида «ActiveState=active» — разбираем в словарь
    service = {}
    for line in state.output.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            service[key.strip()] = value.strip()

    return json_ok(
        {
            "ok": True,
            "time": datetime.now(UTC).isoformat(),
            "version": version.output.strip() if version.ok else None,
            "version_error": None if version.ok else version.output.strip()[:200],
            "service": service or {"error": state.output.strip()[:200]},
            "counts": counts,
        }
    )


async def handle_ops_logs(request: web.Request) -> web.Response:
    """Хвост журнала — тот же, что отдаёт команда /logs в чате."""
    if (refusal := ops_allowed(request)) is not None:
        return refusal

    try:
        lines = int(request.query.get("lines", LOGS_DEFAULT))
    except (TypeError, ValueError):
        lines = LOGS_DEFAULT

    result = await request.app[OPS].logs(lines)
    return json_ok({"ok": result.ok, "code": result.code, "text": result.output})


async def handle_admin_orders(request: web.Request) -> web.Response:
    """Стол заявок: что пришло, что в работе, что отвечено."""
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        found = await repo.open_orders(limit=50)
        # Свежие отвеченные тоже нужны: по ним смотрят, что написали вчера
        recent = [
            order
            for order in await repo.last_orders(limit=20)
            if order.status not in (OrderStatus.NEW, OrderStatus.IN_PROGRESS)
        ]
        rows = []
        for order in [*found, *recent]:
            user = await repo.get_user(order.user_id)
            rows.append(
                {
                    **order_json(order, settings),
                    "client": {
                        "id": order.user_id,
                        "name": user.first_name if user else None,
                        "username": user.username if user else None,
                        "contact": order.contact,
                    },
                }
            )

    return json_ok({"orders": rows})


async def handle_admin_order_one(request: web.Request) -> web.Response:
    """Заявка глазами таролога: вопрос, переписка и кто спрашивает."""
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]

    try:
        order_id = int(request.match_info["order_id"])
    except (TypeError, ValueError):
        return json_error("Заявка не найдена", 404, "unknown")

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(order_id)
        if order is None:
            return json_error("Заявка не найдена", 404, "unknown")
        thread = await repo.order_messages(order.id)
        user = await repo.get_user(order.user_id)

    return json_ok(
        {
            **order_json(order, settings),
            "client": {
                "id": order.user_id,
                "name": user.first_name if user else None,
                "username": user.username if user else None,
                "contact": order.contact,
            },
            "thread": [
                {
                    "author": item.author,
                    "text": item.text,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in thread
            ],
        }
    )


async def handle_admin_order_take(request: web.Request) -> web.Response:
    """Взять заявку в работу: человек видит, что ею занялись."""
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(int(request.match_info["order_id"]))
        if order is None:
            return json_error("Заявка не найдена", 404, "unknown")
        await repo.set_order_status(order, OrderStatus.IN_PROGRESS, admin_id=identity.user.id)
        return json_ok(order_json(order, settings))


async def handle_admin_order_answer(request: web.Request) -> web.Response:
    """Ответ из приложения: уходит человеку в чат и остаётся в заявке."""
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    bots: BotProvider = request.app[BOTS]

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    answer = (payload.get("text") or "").strip()
    if not answer:
        return json_error("Ответ пустой", 400, "empty")

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(int(request.match_info["order_id"]))
        if order is None:
            return json_error("Заявка не найдена", 404, "unknown")

        bot = await bots(identity.tenant_id)
        if bot is None:
            return json_error("Бот недоступен", 503, "no_bot")

        # Сначала доставка, потом запись: заявка, отмеченная отвеченной,
        # но не дошедшая до человека — худший из возможных исходов
        if not await order_rules.deliver(bot, settings, order, answer):
            return json_error(
                "Человеку не доставить — возможно, он закрыл бота", 502, "undelivered"
            )
        await order_rules.remember(repo, order, answer, identity.user.id)
        return json_ok(order_json(order, settings))


async def handle_admin_order_decline(request: web.Request) -> web.Response:
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    bots: BotProvider = request.app[BOTS]

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        order = await repo.get_order(int(request.match_info["order_id"]))
        if order is None:
            return json_error("Заявка не найдена", 404, "unknown")

        bot = await bots(identity.tenant_id)
        if bot is None:
            return json_error("Бот недоступен", 503, "no_bot")
        await order_rules.decline(bot, settings, repo, order, identity.user.id)
        return json_ok(order_json(order, settings))


async def handle_admin_referrals(request: web.Request) -> web.Response:
    """Кто сколько привёл и докуда дошли приглашённые."""
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    async with request.app[SESSIONS]() as session:
        rows = await Repo(session, tenant_id=identity.tenant_id).inviters()
    return json_ok({"inviters": rows})


async def handle_admin_stats(request: web.Request) -> web.Response:
    """Цифры хозяина: воронка, деньги, люди.

    Период задаётся днями: 30 — месяц, 0 — всё время. Проценты считает
    приложение, а здесь только то, что посчитано по базе.
    """
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]

    try:
        days = int(request.query.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = max(0, min(days, 3650))
    since = None if days == 0 else datetime.now(UTC) - timedelta(days=days)

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        steps = await repo.funnel(tuple(name for name, _ in analytics.FUNNEL), since)
        by_tier = await repo.orders_by_tier(since)
        paid = await repo.payments_summary(since)
        totals = await repo.stats()
        subscribers = await repo.count_daily_subscribers()

    return json_ok(
        {
            "days": days,
            "funnel": [
                {"key": name, "title": label, "count": steps.get(name, 0)}
                for name, label in analytics.FUNNEL
            ],
            "tiers": [
                {
                    "key": key,
                    "title": tier_label(settings, key),
                    "count": count,
                }
                for key, count in by_tier.items()
            ],
            # Рубли и звёзды показываются рядом, а не одной суммой
            "paid": {
                "count": paid["count"],
                "money": billing.money(paid["totals"], settings),
            },
            "totals": {**totals, "daily_subscribers": subscribers},
        }
    )


async def handle_promos_list(request: web.Request) -> web.Response:
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    async with request.app[SESSIONS]() as session:
        codes = await Repo(session, tenant_id=identity.tenant_id).promos()
    return json_ok({"codes": [promo_json(code) for code in codes]})


# Щедрость с опечаткой — это подарок, который не отменить: ограничиваем заранее
MAX_READINGS = 100
MAX_USES = 100000


async def handle_promo_create(request: web.Request) -> web.Response:
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    code = promo_rules.normalize(str(payload.get("code") or ""))
    if not code:
        return json_error("Нужно слово для кода", 400, "empty")

    try:
        readings = int(payload.get("readings") or 0)
    except (TypeError, ValueError):
        readings = 0
    if not 1 <= readings <= MAX_READINGS:
        return json_error(f"Раскладов должно быть от 1 до {MAX_READINGS}", 400, "readings")

    try:
        uses_limit = int(payload.get("uses_limit") or 0)
    except (TypeError, ValueError):
        uses_limit = -1
    if not 0 <= uses_limit <= MAX_USES:
        return json_error("Ограничение задано неверно", 400, "uses_limit")

    expires = None
    raw_date = (payload.get("expires_at") or "").strip()
    if raw_date:
        try:
            expires = date.fromisoformat(raw_date)
        except ValueError:
            return json_error("Дата в виде ГГГГ-ММ-ДД", 400, "expires_at")

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        if await repo.promo_by_code(code) is not None:
            return json_error("Такой код уже есть", 409, "exists")
        promo = await repo.add_promo(
            code, readings, uses_limit, expires, (payload.get("note") or "").strip() or None
        )
        created = promo_json(promo)

    return json_ok(created, status=201)


async def handle_promo_toggle(request: web.Request) -> web.Response:
    """Код выключается, но не удаляется: кому-то его уже обещали."""
    if (refusal := deny_stranger(request)) is not None:
        return refusal

    identity: Identity = request[IDENTITY]
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    async with request.app[SESSIONS]() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        codes = {item.id: item for item in await repo.promos(limit=200)}
        promo = codes.get(int(request.match_info["promo_id"]))
        if promo is None:
            return json_error("Такого кода нет", 404, "unknown")
        await repo.set_promo_active(promo, bool(payload.get("active")))
        return json_ok(promo_json(promo))


async def handle_invoice(request: web.Request) -> web.Response:
    """Ссылка на оплату. Открывает её само приложение — Telegram умеет это сам."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    bots: BotProvider = request.app[BOTS]

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    natal = (payload.get("kind") or "auto") == "natal"
    deal = billing.terms(settings, PAYLOAD_NATAL if natal else PAYLOAD_AUTO)
    if deal is None:
        return json_error("Автор ещё не подключил приём оплаты", 409, "payment_not_ready")

    bot = await bots(identity.tenant_id)
    if bot is None:
        return json_error("Бот недоступен", 503, "no_bot")

    link = await bot.create_invoice_link(**billing.invoice_kwargs(settings, deal))
    return json_ok({"link": link, "price": deal.price, "currency": deal.unit})


@dataclass
class Pending:
    """Расклад, который лёг, но разбор к нему ещё не заказан."""

    started: reading_rules.Started
    spread_key: str
    question: str | None
    user_id: int
    tenant_id: int
    at: float


def sweep(pending: dict[str, Pending]) -> None:
    """Выбрасывает забытые расклады: иначе словарь растёт всю жизнь процесса."""
    edge = time.time() - PENDING_TTL
    for token in [key for key, item in pending.items() if item.at < edge]:
        pending.pop(token, None)


async def handle_reading_start(request: web.Request) -> web.Response:
    """Карты ложатся. Разбор пишется следующим запросом — он долгий."""
    identity: Identity = request[IDENTITY]
    settings: Settings = request[BRAND]
    session_factory = request.app[SESSIONS]

    try:
        payload = await request.json()
    except Exception:
        return json_error("Не разобрал запрос")

    key = str(payload.get("spread") or "")
    if key not in SPREADS:
        return json_error("Нет такой раскладки")
    spread = get_spread(key)

    question = (payload.get("question") or "").strip() or None
    if question and len(question) > settings.max_question_length:
        return json_error(f"Вопрос длиннее {settings.max_question_length} символов")
    if spread.yes_no and not question:
        return json_error("Для ответа «да или нет» нужен вопрос")

    try:
        number = int(payload.get("number") or 0)
    except (TypeError, ValueError):
        return json_error("Число не похоже на число")
    if not 1 <= number <= NUMBER_LIMIT:
        number = secrets.randbelow(NUMBER_LIMIT) + 1

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        await repo.upsert_user(
            identity.user.id,
            identity.user.username,
            identity.user.first_name,
            subscribe_new=settings.daily_subscribe_new,
        )
        await repo.log_event(identity.user.id, analytics.AUTO_OPEN)
        decision = await reading_rules.start(
            repo, settings, identity.user.id, spread, question, number=number
        )

    if not decision.allowed:
        # Отказ — это не ошибка: приложению нужно показать, чего не хватает
        return json_ok(
            {
                "allowed": False,
                "reason": decision.reason,
                "free_left": decision.free_left,
                "limit": decision.limit,
            }
        )

    token = secrets.token_urlsafe(16)
    pending: dict[str, Pending] = request.app[PENDING]
    sweep(pending)
    pending[token] = Pending(
        started=decision,
        spread_key=spread.key,
        question=question,
        user_id=identity.user.id,
        tenant_id=identity.tenant_id,
        at=time.time(),
    )

    return json_ok(
        {
            "allowed": True,
            "token": token,
            "spread": spread_payload(spread.key),
            "number": number,
            "question": question,
            "cards": [{**card_payload(card), "position": card.position} for card in decision.drawn],
        }
    )


async def handle_reading_finish(request: web.Request) -> web.Response:
    """Разбор. Может писаться до минуты, поэтому отдельным запросом."""
    identity: Identity = request[IDENTITY]
    session_factory = request.app[SESSIONS]
    interpreter: Interpreter = request.app[INTERPRETER]
    pending: dict[str, Pending] = request.app[PENDING]

    token = request.match_info["token"]
    item = pending.get(token)
    if item is None:
        return json_error("Расклад устарел, разложите заново", 404, "expired")
    # Чужой расклад по чужому ключу забрать нельзя
    if item.user_id != identity.user.id or item.tenant_id != identity.tenant_id:
        return json_error("Это не ваш расклад", 403, "forbidden")

    spread = get_spread(item.spread_key)
    settings: Settings = request[BRAND]
    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        done = await reading_rules.finish(
            repo,
            interpreter,
            identity.user.id,
            spread,
            item.question,
            item.started,
            settings,
        )
        # Остаток берём у тех же правил, что и доступ: у человека могут быть
        # расклады за приглашённых друзей, и своя арифметика их потеряет
        free_left = (await check_access(repo, settings, identity.user.id)).free_left

    if done.rewarded is not None and done.rewarded[1]:
        await thank_the_inviter(request, identity.tenant_id, *done.rewarded)

    pending.pop(token, None)
    result = done.interpretation
    return json_ok({"text": result.text, "ai": result.ai_used, "free_left": free_left})


async def thank_the_inviter(
    request: web.Request, tenant_id: int, inviter_id: int, bonus: int
) -> None:
    """Говорит пригласившему, что ему начислено. Молчит, если сказать нечем.

    Расклад сделан в приложении, а благодарность всё равно приходит в
    переписку: другого места у бота нет, и человек ждёт её именно там.
    """
    bots: BotProvider = request.app[BOTS]
    bot = await bots(tenant_id)
    if bot is None:
        return
    try:
        await bot.send_message(inviter_id, texts.INVITE_REWARDED.format(bonus=bonus))
    except Exception as exc:  # noqa: BLE001 - чужая переписка нам не подвластна
        logger.info("Пригласившему %s не написать: %s", inviter_id, exc)


async def _no_bot(tenant_id: int) -> Bot | None:
    return None


def create_app(
    settings: Settings,
    session_factory,
    tokens: TokensProvider,
    brands: BrandProvider,
    interpreter: Interpreter | None = None,
    bots: BotProvider | None = None,
    ops: ServerOps | None = None,
) -> web.Application:
    app = web.Application(
        middlewares=[errors_middleware, auth_middleware(tokens, brands)],
        client_max_size=256 * 1024,
    )
    app[SESSIONS] = session_factory
    app[BRAND] = settings
    # Без ключа толкователь отдаёт базовые значения карт — приложение работает,
    # просто разбор получается суше
    app[INTERPRETER] = interpreter or Interpreter(
        GrokClient(settings.ai_api_key, base_url=settings.ai_base_url, model=settings.ai_model),
        author_name=settings.author_name,
        deadline=settings.grok_deadline,
    )
    app[PENDING] = {}
    # Заявки и счета уходят через бота того же арендатора
    app[BOTS] = bots or _no_bot
    # Присмотр снаружи спрашивает то же, что команды в чате
    app[OPS] = ops or ServerOps(settings.service_name, settings.update_script)

    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/config", handle_config)
    app.router.add_get("/api/spreads", handle_spreads)
    app.router.add_get("/api/me", handle_me)
    app.router.add_get("/api/daily", handle_daily_get)
    app.router.add_post("/api/daily", handle_daily_draw)
    app.router.add_post("/api/daily/subscription", handle_daily_subscription)
    app.router.add_post("/api/reading", handle_reading_start)
    app.router.add_post("/api/reading/{token}/interpretation", handle_reading_finish)
    app.router.add_get("/api/cities", handle_cities)
    app.router.add_put("/api/profile", handle_profile_save)
    app.router.add_get("/api/chart", handle_chart)
    app.router.add_get("/api/forecast", handle_forecast)
    app.router.add_post("/api/forecast", handle_forecast_text)
    app.router.add_get("/api/natal", handle_natal_get)
    app.router.add_post("/api/natal", handle_natal_write)
    app.router.add_post("/api/order", handle_order_create)
    app.router.add_delete("/api/order/{order_id}", handle_order_cancel)
    app.router.add_get("/api/readings/{reading_id}", handle_reading_one)
    app.router.add_get("/api/orders", handle_orders_list)
    app.router.add_get("/api/orders/{order_id}", handle_order_one)
    app.router.add_post("/api/orders/{order_id}/followup", handle_order_followup)
    app.router.add_get("/api/invite", handle_invite)
    app.router.add_get("/api/ops/status", handle_ops_status)
    app.router.add_get("/api/ops/logs", handle_ops_logs)
    app.router.add_get("/api/admin/orders", handle_admin_orders)
    app.router.add_get("/api/admin/orders/{order_id}", handle_admin_order_one)
    app.router.add_post("/api/admin/orders/{order_id}/take", handle_admin_order_take)
    app.router.add_post("/api/admin/orders/{order_id}/answer", handle_admin_order_answer)
    app.router.add_post("/api/admin/orders/{order_id}/decline", handle_admin_order_decline)
    app.router.add_get("/api/admin/stats", handle_admin_stats)
    app.router.add_get("/api/admin/referrals", handle_admin_referrals)
    app.router.add_get("/api/admin/promos", handle_promos_list)
    app.router.add_post("/api/admin/promos", handle_promo_create)
    app.router.add_post("/api/admin/promos/{promo_id}/active", handle_promo_toggle)
    app.router.add_post("/api/promo", handle_promo)
    app.router.add_post("/api/invoice", handle_invoice)
    return app


async def serve(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Поднимает сервер и возвращает runner — его останавливает вызывающий."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("API мини-приложения слушает %s:%s", host, port)
    return runner
