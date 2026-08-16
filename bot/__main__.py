"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import Settings, get_settings
from .db import create_engine, create_session_factory, init_models
from .handlers import build_router
from .middlewares import DbSessionMiddleware, ThrottlingMiddleware, UserMiddleware
from .services import GrokClient, Interpreter

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Меню"),
    BotCommand(command="help", description="Как пользоваться"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
]


def build_dispatcher(
    settings: Settings,
    interpreter: Interpreter,
    session_factory,
    *,
    throttle_rate: float = 0.7,
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings
    dp["interpreter"] = interpreter

    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.update.middleware(UserMiddleware())
    if throttle_rate > 0:
        dp.message.middleware(ThrottlingMiddleware(throttle_rate))
        dp.callback_query.middleware(ThrottlingMiddleware(throttle_rate))

    dp.include_router(build_router())
    return dp


async def main() -> None:
    settings = get_settings()
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
        settings.xai_api_key,
        base_url=settings.xai_base_url,
        model=settings.grok_model,
        timeout=settings.grok_timeout,
        max_tokens=settings.grok_max_tokens,
        temperature=settings.grok_temperature,
    )
    interpreter = Interpreter(grok, author_name=settings.author_name)

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(settings, interpreter, session_factory)

    try:
        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот запущен")
        await dp.start_polling(bot)
    finally:
        await grok.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
