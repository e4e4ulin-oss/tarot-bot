"""Заведение франчайзи с сервера.

Токен чужого бота — секрет, и в переписке ему не место, поэтому арендатор
заводится здесь, а не командой в чате. В боте владелец видит только сводку.

    python -m bot.cli add --slug nadezhda --title "Надежда" --token 123:abc \\
        --author "Надежда" --price-full 1500

    python -m bot.cli list
    python -m bot.cli disable --slug nadezhda

Цены и правила меняются там же, по одному полю за раз:

    python -m bot.cli set --slug origin --field free_auto_readings --value 5
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from .config import get_settings
from .db import Repo, Tenant
from .db.session import create_engine, create_session_factory, init_models
from .tenants import EDITABLE, bot_id_of, cast_field


async def add(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_models(engine)
    factory = create_session_factory(engine)

    async with factory() as session:
        taken = await session.scalar(select(Tenant).where(Tenant.slug == args.slug))
        if taken is not None:
            print(f"Арендатор «{args.slug}» уже есть")
            return 1

        tenant = Tenant(
            slug=args.slug,
            title=args.title,
            bot_token=args.token,
            bot_id=bot_id_of(args.token),
            admin_chat_id=args.admin_chat_id or 0,
            admin_ids=[args.admin_chat_id] if args.admin_chat_id else [],
            author_name=args.author or args.title,
            currency=args.currency,
            price_short=args.price_short,
            price_base=args.price_base,
            price_full=args.price_full,
            auto_reading_stars=args.auto_stars,
            free_auto_readings=args.free,
            miniapp_url=args.miniapp_url,
        )
        session.add(tenant)
        await session.commit()

    await engine.dispose()
    print(f"Арендатор «{args.slug}» заведён. Перезапустите службу, чтобы бот поднялся.")
    return 0


async def show(_: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_models(engine)
    factory = create_session_factory(engine)

    async with factory() as session:
        for row in await Repo(session).tenants_overview():
            state = "работает" if row["active"] else "выключен"
            print(
                f"{row['slug']:<16} {row['title']:<20} {state:<10} "
                f"людей {row['users']:<5} раскладов {row['readings']:<5} "
                f"оплат {row['paid']} на {row['total']}"
            )
    await engine.dispose()
    return 0


async def switch(args: argparse.Namespace, active: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)

    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == args.slug))
        if tenant is None:
            print(f"Нет арендатора «{args.slug}»")
            return 1
        tenant.active = active
        await session.commit()
    await engine.dispose()
    print(f"«{args.slug}»: {'включён' if active else 'выключен'}. Нужен перезапуск службы.")
    return 0


async def edit(args: argparse.Namespace) -> int:
    """Меняет одно поле арендатора. Бот подхватит значение при перезапуске."""
    if args.field not in EDITABLE:
        print(f"Менять можно: {', '.join(sorted(EDITABLE))}")
        return 2

    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_models(engine)
    factory = create_session_factory(engine)

    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == args.slug))
        if tenant is None:
            print(f"Нет арендатора «{args.slug}»")
            return 1

        was = getattr(tenant, args.field)
        setattr(tenant, args.field, cast_field(args.field, args.value))
        await session.commit()
        print(f"{args.slug}: {args.field} {was} → {getattr(tenant, args.field)}")

    await engine.dispose()
    print("Перезапустите бота, чтобы значение вступило в силу: systemctl restart tarot-bot")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Арендаторы сервиса")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("add", help="завести франчайзи")
    new.add_argument("--slug", required=True, help="короткое имя латиницей")
    new.add_argument("--title", required=True, help="как называется бренд")
    new.add_argument("--token", required=True, help="токен его бота от BotFather")
    new.add_argument("--author", help="как бот называет автора разборов")
    new.add_argument("--admin-chat-id", type=int, default=0, help="куда слать заявки")
    new.add_argument("--currency", default="₽")
    new.add_argument("--price-short", type=int, default=500)
    new.add_argument("--price-base", type=int, default=700)
    new.add_argument("--price-full", type=int, default=1000)
    new.add_argument("--auto-stars", type=int, default=50, help="цена расклада в звёздах")
    new.add_argument("--free", type=int, default=5, help="бесплатных раскладов")
    new.add_argument("--miniapp-url", default="", help="адрес его мини-приложения")

    commands.add_parser("list", help="показать арендаторов")

    change = commands.add_parser("set", help="изменить цену или правило")
    change.add_argument("--slug", required=True)
    change.add_argument("--field", required=True, help=f"одно из: {', '.join(sorted(EDITABLE))}")
    change.add_argument("--value", required=True)

    off = commands.add_parser("disable", help="выключить бота франчайзи")
    off.add_argument("--slug", required=True)

    on = commands.add_parser("enable", help="включить обратно")
    on.add_argument("--slug", required=True)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "add":
        return asyncio.run(add(args))
    if args.command == "list":
        return asyncio.run(show(args))
    if args.command == "set":
        return asyncio.run(edit(args))
    if args.command == "disable":
        return asyncio.run(switch(args, False))
    return asyncio.run(switch(args, True))


if __name__ == "__main__":
    raise SystemExit(main())
