"""Работа с данными: всё обращение к БД собрано здесь, хендлеры SQL не пишут."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CardImage, Order, OrderStatus, Reading, ReadingMode, User, utcnow


class Repo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Пользователи ---------------------------------------------------
    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            user = User(id=user_id, username=username, first_name=first_name)
            self.session.add(user)
        else:
            user.username = username
            user.first_name = first_name
        await self.session.commit()
        return user

    async def get_user(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    # --- Расклады -------------------------------------------------------
    async def save_reading(
        self,
        *,
        user_id: int,
        spread_key: str,
        question: str | None,
        cards: list[dict],
        interpretation: str,
        ai_used: bool,
        mode: ReadingMode = ReadingMode.AUTO,
    ) -> Reading:
        reading = Reading(
            user_id=user_id,
            spread_key=spread_key,
            question=question,
            cards=cards,
            interpretation=interpretation,
            ai_used=ai_used,
            mode=mode,
        )
        self.session.add(reading)
        await self.session.commit()
        return reading

    async def count_readings_since(self, user_id: int, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(Reading)
            .where(Reading.user_id == user_id, Reading.created_at >= since)
        )
        return int(await self.session.scalar(stmt) or 0)

    async def count_readings_today(self, user_id: int) -> int:
        since = datetime.now(UTC) - timedelta(days=1)
        return await self.count_readings_since(user_id, since)

    async def last_readings(self, user_id: int, limit: int = 5) -> list[Reading]:
        stmt = (
            select(Reading)
            .where(Reading.user_id == user_id)
            .order_by(Reading.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def get_reading(self, reading_id: int) -> Reading | None:
        return await self.session.get(Reading, reading_id)

    # --- Кэш картинок карт ----------------------------------------------
    async def card_file_ids(self, card_ids: list[str]) -> dict[str, str]:
        if not card_ids:
            return {}
        stmt = select(CardImage).where(CardImage.card_id.in_(card_ids))
        return {row.card_id: row.file_id for row in (await self.session.scalars(stmt)).all()}

    async def save_card_file_id(self, card_id: str, file_id: str) -> None:
        if await self.session.get(CardImage, card_id) is not None:
            return
        self.session.add(CardImage(card_id=card_id, file_id=file_id))
        await self.session.commit()

    # --- Заявки на авторский разбор -------------------------------------
    async def create_order(
        self, *, user_id: int, question: str, topic: str | None, contact: str | None
    ) -> Order:
        order = Order(user_id=user_id, question=question, topic=topic, contact=contact)
        self.session.add(order)
        await self.session.commit()
        return order

    async def get_order(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def open_order_for_user(self, user_id: int) -> Order | None:
        stmt = (
            select(Order)
            .where(
                Order.user_id == user_id,
                Order.status.in_([OrderStatus.NEW, OrderStatus.IN_PROGRESS]),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def last_order_for_user(self, user_id: int) -> Order | None:
        stmt = (
            select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(1)
        )
        return await self.session.scalar(stmt)

    async def open_orders(self, limit: int = 20) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.status.in_([OrderStatus.NEW, OrderStatus.IN_PROGRESS]))
            .order_by(Order.created_at.asc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def set_order_status(
        self, order: Order, status: OrderStatus, *, admin_id: int | None = None
    ) -> Order:
        order.status = status
        if admin_id is not None:
            order.admin_id = admin_id
        if status is OrderStatus.ANSWERED:
            order.answered_at = utcnow()
        await self.session.commit()
        return order

    async def set_admin_message(self, order: Order, message_id: int) -> Order:
        order.admin_message_id = message_id
        await self.session.commit()
        return order

    # --- Статистика для админа ------------------------------------------
    async def stats(self) -> dict[str, int]:
        users = int(await self.session.scalar(select(func.count()).select_from(User)) or 0)
        readings = int(await self.session.scalar(select(func.count()).select_from(Reading)) or 0)
        orders = int(await self.session.scalar(select(func.count()).select_from(Order)) or 0)
        open_orders = int(
            await self.session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.status.in_([OrderStatus.NEW, OrderStatus.IN_PROGRESS]))
            )
            or 0
        )
        return {
            "users": users,
            "readings": readings,
            "orders": orders,
            "open_orders": open_orders,
        }
