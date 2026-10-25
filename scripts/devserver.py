"""Локальный запуск мини-приложения: API и статика на одном адресе.

На сервере статику отдаёт nginx, а здесь её отдаёт сам aiohttp — чтобы открыть
приложение в обычном браузере и посмотреть экраны, не поднимая ни nginx, ни
бота. Подписи Telegram в браузере нет, поэтому запросы с localhost считаются
пользователем из WEB_DEV_USER.

    python scripts/devserver.py            # http://127.0.0.1:8099
    python scripts/devserver.py 8100       # другой порт
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "dev:token")
os.environ.setdefault("WEB_DEV_USER", "1")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{ROOT / 'data' / 'dev.db'}")

from aiohttp import web  # noqa: E402

from bot.api.app import create_app  # noqa: E402
from bot.config import Settings  # noqa: E402
from bot.db import create_engine, create_session_factory, init_models  # noqa: E402

MINIAPP = ROOT / "miniapp"


async def main(port: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings()
    engine = create_engine(settings.database_url)
    await init_models(engine)
    factory = create_session_factory(engine)

    async def tokens() -> dict[int, str]:
        return {1: settings.bot_token}

    async def brands(tenant_id: int) -> Settings:
        return settings

    app = create_app(settings, factory, tokens, brands)

    async def index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(MINIAPP / "index.html")

    app.router.add_get("/", index)
    # Статика последней: иначе она перехватила бы /api
    app.router.add_static("/", MINIAPP, show_index=False)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"\n  мини-приложение: http://127.0.0.1:{port}\n  Ctrl+C — остановить\n")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8099))
