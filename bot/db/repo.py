"""Работа с данными: всё обращение к БД собрано здесь, хендлеры SQL не пишут."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    CardImage,
    DailyCard,
    Event,
    Order,
    OrderStatus,
    Payment,
    Reading,
    ReadingMode,
    Tenant,
    User,
    utcnow,
)


class Repo:
    """Данные одного арендатора.

    Каждый запрос ограничен своим брендом: два франчайзи работают на одной
    базе и не должны видеть ни клиентов друг друга, ни их раскладов. Единица
    по умолчанию — арендатор, заведённый при первом запуске.
    """

    def __init__(self, session: AsyncSession, tenant_id: int = 1) -> None:
        self.session = session
        self.tenant_id = tenant_id

    # --- Платежи и доступ ------------------------------------------------
    async def count_auto_readings(self, user_id: int) -> int:
        """Сколько авто-раскладов человек сделал за всё время."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Reading)
            .where(
                Reading.tenant_id == self.tenant_id,
                Reading.user_id == user_id,
                Reading.mode == ReadingMode.AUTO,
            )
        )
        return int(result.scalar_one())

    async def add_payment(
        self, user_id: int, amount: int, currency: str, charge_id: str | None
    ) -> Payment:
        payment = Payment(
            tenant_id=self.tenant_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            charge_id=charge_id,
        )
        self.session.add(payment)
        await self.session.commit()
        return payment

    async def unspent_payment(self, user_id: int) -> Payment | None:
        result = await self.session.execute(
            select(Payment)
            .where(
                Payment.tenant_id == self.tenant_id,
                Payment.user_id == user_id,
                Payment.spent_at.is_(None),
            )
            .order_by(Payment.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def spend_payment(self, payment: Payment) -> None:
        payment.spent_at = utcnow()
        await self.session.commit()

    async def payments_summary(self, since: datetime | None = None) -> dict[str, int]:
        query = select(func.count(), func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.tenant_id == self.tenant_id
        )
        if since is not None:
            query = query.where(Payment.created_at >= since)
        count, total = (await self.session.execute(query)).one()
        return {"count": int(count), "total": int(total)}

    # --- Пользователи ---------------------------------------------------
    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> User:
        user = await self.session.get(User, (self.tenant_id, user_id))
        if user is None:
            user = User(
                tenant_id=self.tenant_id,
                id=user_id,
                username=username,
                first_name=first_name,
            )
            self.session.add(user)
        else:
            user.username = username
            user.first_name = first_name
        await self.session.commit()
        return user

    async def get_user(self, user_id: int) -> User | None:
        return await self.session.get(User, (self.tenant_id, user_id))

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
            tenant_id=self.tenant_id,
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
            .where(
                Reading.tenant_id == self.tenant_id,
                Reading.user_id == user_id,
                Reading.created_at >= since,
            )
        )
        return int(await self.session.scalar(stmt) or 0)

    async def count_readings_today(self, user_id: int) -> int:
        since = datetime.now(UTC) - timedelta(days=1)
        return await self.count_readings_since(user_id, since)

    async def last_readings(self, user_id: int, limit: int = 5) -> list[Reading]:
        stmt = (
            select(Reading)
            .where(Reading.tenant_id == self.tenant_id, Reading.user_id == user_id)
            .order_by(Reading.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def get_reading(self, reading_id: int) -> Reading | None:
        reading = await self.session.get(Reading, reading_id)
        # Чужой расклад по угаданному номеру отдавать нельзя
        return reading if reading and reading.tenant_id == self.tenant_id else None

    # --- Кэш картинок карт ----------------------------------------------
    async def card_file_ids(self, card_ids: list[str]) -> dict[str, str]:
        if not card_ids:
            return {}
        stmt = select(CardImage).where(
            CardImage.tenant_id == self.tenant_id, CardImage.card_id.in_(card_ids)
        )
        return {row.card_id: row.file_id for row in (await self.session.scalars(stmt)).all()}

    async def save_card_file_id(self, card_id: str, file_id: str) -> None:
        if await self.session.get(CardImage, (self.tenant_id, card_id)) is not None:
            return
        self.session.add(
            CardImage(tenant_id=self.tenant_id, card_id=card_id, file_id=file_id)
        )
        await self.session.commit()

    # --- Заявки на авторский разбор -------------------------------------
    async def create_order(
        self,
        *,
        user_id: int,
        question: str,
        topic: str | None,
        contact: str | None,
        tier: str | None = None,
    ) -> Order:
        order = Order(
            tenant_id=self.tenant_id,
            user_id=user_id,
            question=question,
            topic=topic,
            contact=contact,
            tier=tier,
        )
        self.session.add(order)
        await self.session.commit()
        return order

    async def get_order(self, order_id: int) -> Order | None:
        order = await self.session.get(Order, order_id)
        return order if order and order.tenant_id == self.tenant_id else None

    async def open_order_for_user(self, user_id: int) -> Order | None:
        stmt = (
            select(Order)
            .where(
                Order.tenant_id == self.tenant_id,
                Order.user_id == user_id,
                Order.status.in_([OrderStatus.NEW, OrderStatus.IN_PROGRESS]),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def last_order_for_user(self, user_id: int) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.tenant_id == self.tenant_id, Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def open_orders(self, limit: int = 20) -> list[Order]:
        stmt = (
            select(Order)
            .where(
                Order.tenant_id == self.tenant_id,
                Order.status.in_([OrderStatus.NEW, OrderStatus.IN_PROGRESS]),
            )
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

    async def count_followup(self, order: Order) -> Order:
        order.followups_used = (order.followups_used or 0) + 1
        await self.session.commit()
        return order

    async def set_admin_message(self, order: Order, message_id: int) -> Order:
        order.admin_message_id = message_id
        await self.session.commit()
        return order

    # --- Статистика для админа ------------------------------------------
    # --- Карта дня --------------------------------------------------------
    async def daily_card(self, user_id: int, day: date) -> DailyCard | None:
        return await self.session.scalar(
            select(DailyCard).where(
                DailyCard.tenant_id == self.tenant_id,
                DailyCard.user_id == user_id,
                DailyCard.day == day,
            )
        )

    async def save_daily_card(
        self, user_id: int, day: date, card_id: str, reversed_: bool, *, delivered: bool = False
    ) -> DailyCard:
        card = DailyCard(
            tenant_id=self.tenant_id,
            user_id=user_id,
            day=day,
            card_id=card_id,
            reversed=reversed_,
            delivered=delivered,
        )
        self.session.add(card)
        await self.session.commit()
        return card

    async def mark_daily_delivered(self, card: DailyCard) -> None:
        card.delivered = True
        await self.session.commit()

    async def set_daily_subscription(self, user_id: int, subscribed: bool) -> None:
        await self.session.execute(
            update(User)
            .where(User.tenant_id == self.tenant_id, User.id == user_id)
            .values(daily_subscribed=subscribed)
        )
        await self.session.commit()

    async def is_daily_subscribed(self, user_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(User.daily_subscribed).where(
                    User.tenant_id == self.tenant_id, User.id == user_id
                )
            )
        )

    async def daily_subscribers_pending(self, day: date, limit: int = 50) -> list[int]:
        """Подписчики, которым сегодняшнюю карту ещё не отправляли."""
        already = select(DailyCard.user_id).where(
            DailyCard.tenant_id == self.tenant_id,
            DailyCard.day == day,
            DailyCard.delivered.is_(True),
        )
        rows = await self.session.scalars(
            select(User.id)
            .where(
                User.tenant_id == self.tenant_id,
                User.daily_subscribed.is_(True),
                User.id.not_in(already),
            )
            .order_by(User.id)
            .limit(limit)
        )
        return list(rows)

    async def count_daily_subscribers(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == self.tenant_id, User.daily_subscribed.is_(True))
            )
            or 0
        )

    # --- Воронка ---------------------------------------------------------
    async def log_event(self, user_id: int, name: str, detail: str | None = None) -> None:
        """Отмечает шаг воронки. Ошибка здесь не должна ломать сценарий."""
        self.session.add(
            Event(tenant_id=self.tenant_id, user_id=user_id, name=name, detail=detail)
        )
        await self.session.commit()

    async def funnel(self, names: tuple[str, ...], since: datetime | None = None) -> dict[str, int]:
        """Сколько разных людей дошло до каждого шага."""
        query = (
            select(Event.name, func.count(func.distinct(Event.user_id)))
            .where(Event.tenant_id == self.tenant_id)
            .group_by(Event.name)
        )
        if since is not None:
            query = query.where(Event.created_at >= since)
        rows = (await self.session.execute(query)).all()
        counted = {name: int(count) for name, count in rows}
        return {name: counted.get(name, 0) for name in names}

    async def orders_by_tier(self, since: datetime | None = None) -> dict[str, int]:
        query = (
            select(Order.tier, func.count())
            .where(Order.tenant_id == self.tenant_id)
            .group_by(Order.tier)
        )
        if since is not None:
            query = query.where(Order.created_at >= since)
        rows = (await self.session.execute(query)).all()
        return {(tier or "не выбран"): int(count) for tier, count in rows}

    async def stats(self) -> dict[str, int]:
        def count(model) -> object:
            return select(func.count()).select_from(model).where(model.tenant_id == self.tenant_id)

        users = int(await self.session.scalar(count(User)) or 0)
        readings = int(await self.session.scalar(count(Reading)) or 0)
        orders = int(await self.session.scalar(count(Order)) or 0)
        open_orders = int(
            await self.session.scalar(
                count(Order).where(Order.status.in_([OrderStatus.NEW, OrderStatus.IN_PROGRESS]))
            )
            or 0
        )
        return {
            "users": users,
            "readings": readings,
            "orders": orders,
            "open_orders": open_orders,
        }

    # --- Арендаторы -------------------------------------------------------
    async def tenants_overview(self) -> list[dict[str, object]]:
        """Сводка для владельца сервиса: кто работает и сколько принёс.

        Запрос намеренно не ограничен арендатором: это единственное место,
        где смотрят поверх всех брендов, и смотрит владелец.
        """
        rows = (await self.session.execute(select(Tenant).order_by(Tenant.id))).scalars().all()
        overview = []
        for tenant in rows:
            users = await self.session.scalar(
                select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
            )
            readings = await self.session.scalar(
                select(func.count()).select_from(Reading).where(Reading.tenant_id == tenant.id)
            )
            paid, total = (
                await self.session.execute(
                    select(func.count(), func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.tenant_id == tenant.id
                    )
                )
            ).one()
            overview.append(
                {
                    "slug": tenant.slug,
                    "title": tenant.title,
                    "active": tenant.active,
                    "users": int(users or 0),
                    "readings": int(readings or 0),
                    "paid": int(paid or 0),
                    "total": int(total or 0),
                }
            )
        return overview
