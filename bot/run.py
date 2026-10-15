"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from pydantic import ValidationError

from .config import Settings, describe_missing_settings, get_settings
from .db import create_engine, create_session_factory, init_models
from .handlers import build_router
from .middlewares import (
    DbSessionMiddleware,
    TenantMiddleware,
    ThrottlingMiddleware,
    UserMiddleware,
)
from .services import GrokClient, Interpreter, ServerOps
from .services.daily import DailyCards
from .tenants import active_tenants, ensure_default, settings_for

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Меню"),
    BotCommand(command="daily", description="Карта дня"),
    BotCommand(command="deck", description="Колода целиком"),
    BotCommand(command="profile", description="Личный кабинет"),
    BotCommand(command="chart", description="Натальная карта"),
    BotCommand(command="forecast", description="Астрологический прогноз"),
    BotCommand(command="help", description="Как пользоваться"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
]


def build_dispatcher(
    settings: Settings,
    interpreter: Interpreter,
    session_factory,
    *,
    throttle_rate: float = 0.7,
    server: ServerOps | None = None,
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings
    dp["interpreter"] = interpreter
    dp["server"] = server or ServerOps(settings.service_name, settings.update_script)

    dp.update.middleware(DbSessionMiddleware(session_factory))
    # Порядок важен: арендатор ищется по уже открытой сессии
    dp.update.middleware(TenantMiddleware(settings))
    dp.update.middleware(UserMiddleware())
    if throttle_rate > 0:
        dp.message.middleware(ThrottlingMiddleware(throttle_rate))
        dp.callback_query.middleware(ThrottlingMiddleware(throttle_rate))

    dp.include_router(build_router())
    return dp


async def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise SystemExit(describe_missing_settings(exc)) from None

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not settings.ai_enabled:
        logger.warning(
            "XAI_API_KEY не задан — авто-разбор будет отдавать базовые значения карт без ИИ"
        )
    if not settings.admin_chat_id:
        logger.warning("ADMIN_CHAT_ID не задан — заявки на авторский разбор некуда отправлять")

    engine = create_engine(settings.database_url)
    await init_models(engine)
    session_factory = create_session_factory(engine)

    grok = GrokClient(
        settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        timeout=settings.grok_timeout,
        max_tokens=settings.grok_max_tokens,
        temperature=settings.grok_temperature,
        retries=settings.grok_retries,
    )
    interpreter = Interpreter(
        grok, author_name=settings.author_name, deadline=settings.grok_deadline
    )

    async with session_factory() as session:
        await ensure_default(session, settings)
        tenants = await active_tenants(session)

    if not tenants:
        raise SystemExit(
            "Нет ни одного арендатора: заведите бота в таблице tenants "
            "или задайте BOT_TOKEN, и он станет арендатором по умолчанию."
        )

    bots = [
        Bot(tenant.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        for tenant in tenants
    ]
    dp = build_dispatcher(settings, interpreter, session_factory)

    tasks: list[asyncio.Task] = []
    try:
        for bot, tenant in zip(bots, tenants, strict=True):
            await bot.set_my_commands(COMMANDS)
            await bot.delete_webhook(drop_pending_updates=True)
            if settings.daily_card_enabled:
                # У каждого арендатора своя рассылка: свой бот и свои подписчики
                brand = settings_for(tenant, settings)
                tasks.append(
                    asyncio.create_task(
                        DailyCards(bot, brand, session_factory, tenant_id=tenant.id).run()
                    )
                )
            logger.info("Бот «%s» запущен", tenant.title)

        if settings.daily_card_enabled:
            logger.info("Карта дня: рассылка в %s:00", settings.daily_card_hour)
        await dp.start_polling(*bots)
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await grok.close()
        for bot in bots:
            await bot.session.close()
        await engine.dispose()


def run() -> int:
    """Точка входа. Вызывается из bot/__main__.py, который ловит падения."""
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    return 0
