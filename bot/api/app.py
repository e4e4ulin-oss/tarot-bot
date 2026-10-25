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
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from .. import analytics
from ..config import Settings
from ..db import Repo
from ..schedule import now
from ..services.daily import card_of_the_day
from ..tarot import SPREAD_ORDER, SPREADS, DrawnCard
from ..tiers import tiers as author_tiers
from .auth import AuthError, Identity, WebAppUser, init_data_from_header, verify

logger = logging.getLogger(__name__)

# Ключи в словаре запроса: кто пришёл и чьи настройки применять
IDENTITY = web.AppKey("identity", Identity)
BRAND = web.AppKey("brand", Settings)
SESSIONS = web.AppKey("session_factory", object)

# Эти адреса отвечают без подписи: по ним приложение решает, что показывать
# на экране входа, ещё не зная, кто перед ним.
PUBLIC_PATHS = frozenset({"/api/health", "/api/config"})

TokensProvider = Callable[[], Awaitable[dict[int, str]]]
BrandProvider = Callable[[int], Awaitable[Settings]]


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
        if request.path in PUBLIC_PATHS:
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
            "auto_price": settings.auto_reading_price,
            "natal_price": settings.natal_reading_price,
            "free_readings": settings.free_auto_readings,
            "free_needs_daily": settings.free_needs_daily,
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
            identity.user.id, identity.user.username, identity.user.first_name
        )
        used = await repo.count_auto_readings(identity.user.id)
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        history = await repo.last_readings(identity.user.id, limit=10)
        order = await repo.open_order_for_user(identity.user.id)

    left = max(0, settings.free_auto_readings - used)
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
            "readings": {
                "used": used,
                "free_left": left,
                "free_limit": settings.free_auto_readings,
                "needs_daily": settings.free_needs_daily and not subscribed,
            },
            "history": [
                {
                    "id": item.id,
                    "mode": item.mode.value if hasattr(item.mode, "value") else str(item.mode),
                    "spread": item.spread,
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
        await repo.upsert_user(identity.user.id, identity.user.username, identity.user.first_name)
        drawn = await card_of_the_day(repo, settings, identity.user.id, day)
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        await repo.log_event(identity.user.id, analytics.DAILY_OPEN)

    return json_ok({"card": card_payload(drawn), "subscribed": subscribed})


async def handle_daily_subscription(request: web.Request) -> web.Response:
    """Переключатель утренней рассылки: состояние возвращаем новое."""
    identity: Identity = request[IDENTITY]
    session_factory = request.app[SESSIONS]

    async with session_factory() as session:
        repo = Repo(session, tenant_id=identity.tenant_id)
        # В боте пользователя заводит middleware, здесь его может ещё не быть:
        # подписаться можно первым же действием, не открыв больше ничего
        await repo.upsert_user(identity.user.id, identity.user.username, identity.user.first_name)
        subscribed = await repo.is_daily_subscribed(identity.user.id)
        await repo.set_daily_subscription(identity.user.id, not subscribed)
        if not subscribed:
            await repo.log_event(identity.user.id, analytics.DAILY_SUBSCRIBED)

    return json_ok({"subscribed": not subscribed})


def create_app(
    settings: Settings,
    session_factory,
    tokens: TokensProvider,
    brands: BrandProvider,
) -> web.Application:
    app = web.Application(
        middlewares=[errors_middleware, auth_middleware(tokens, brands)],
        client_max_size=256 * 1024,
    )
    app[SESSIONS] = session_factory
    app[BRAND] = settings

    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/config", handle_config)
    app.router.add_get("/api/spreads", handle_spreads)
    app.router.add_get("/api/me", handle_me)
    app.router.add_get("/api/daily", handle_daily_get)
    app.router.add_post("/api/daily", handle_daily_draw)
    app.router.add_post("/api/daily/subscription", handle_daily_subscription)
    return app


async def serve(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Поднимает сервер и возвращает runner — его останавливает вызывающий."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("API мини-приложения слушает %s:%s", host, port)
    return runner
