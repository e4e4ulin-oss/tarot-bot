"""Работа с данными: всё обращение к БД собрано здесь, хендлеры SQL не пишут."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    CardImage,
    DailyCard,
    Event,
    GuessGame,
    Order,
    OrderMessage,
    OrderStatus,
    Payment,
    PromoCode,
    PromoUse,
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
        self,
        user_id: int,
        amount: int,
        currency: str,
        charge_id: str | None,
        kind: str = "auto_reading",
    ) -> Payment:
        payment = Payment(
            tenant_id=self.tenant_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            charge_id=charge_id,
            kind=kind,
        )
        self.session.add(payment)
        await self.session.commit()
        return payment

    async def unspent_payment(self, user_id: int, kind: str = "auto_reading") -> Payment | None:
        result = await self.session.execute(
            select(Payment)
            .where(
                Payment.tenant_id == self.tenant_id,
                Payment.user_id == user_id,
                Payment.kind == kind,
                Payment.spent_at.is_(None),
                Payment.refunded_at.is_(None),
            )
            .order_by(Payment.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def payment_by_charge(self, charge_id: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment).where(
                Payment.tenant_id == self.tenant_id, Payment.charge_id == charge_id
            )
        )
        return result.scalar_one_or_none()

    async def last_payments(self, limit: int = 10) -> list[Payment]:
        result = await self.session.execute(
            select(Payment)
            .where(Payment.tenant_id == self.tenant_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def mark_refunded(self, payment: Payment) -> None:
        payment.refunded_at = utcnow()
        await self.session.commit()

    async def spend_payment(self, payment: Payment) -> None:
        payment.spent_at = utcnow()
        await self.session.commit()

    async def payments_summary(self, since: datetime | None = None) -> dict[str, object]:
        """Сколько платежей и на какие суммы — отдельно по каждой валюте.

        Рубли и звёзды в одно число не складываются, поэтому итог приходит
        словарём: валюта → сумма.
        """
        query = select(
            Payment.currency, func.count(), func.coalesce(func.sum(Payment.amount), 0)
        ).where(Payment.tenant_id == self.tenant_id, Payment.refunded_at.is_(None))
        if since is not None:
            query = query.where(Payment.created_at >= since)
        rows = (await self.session.execute(query.group_by(Payment.currency))).all()

        totals = {str(currency or "RUB"): int(total) for currency, _, total in rows}
        return {"count": sum(int(count) for _, count, _ in rows), "totals": totals}

    # --- Партии «карты читают задуманного» ------------------------------
    async def active_game(self, user_id: int) -> GuessGame | None:
        """Незаконченная партия. Больше одной такой у человека не бывает."""
        result = await self.session.execute(
            select(GuessGame)
            .where(
                GuessGame.tenant_id == self.tenant_id,
                GuessGame.user_id == user_id,
                GuessGame.status == "active",
            )
            .order_by(GuessGame.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def last_game(self, user_id: int) -> GuessGame | None:
        result = await self.session.execute(
            select(GuessGame)
            .where(GuessGame.tenant_id == self.tenant_id, GuessGame.user_id == user_id)
            .order_by(GuessGame.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_games(self, user_id: int) -> int:
        """Сколько партий человек уже начинал: первая бесплатна."""
        result = await self.session.execute(
            select(func.count())
            .select_from(GuessGame)
            .where(GuessGame.tenant_id == self.tenant_id, GuessGame.user_id == user_id)
        )
        return int(result.scalar_one())

    async def start_game(self, user_id: int) -> GuessGame:
        game = GuessGame(tenant_id=self.tenant_id, user_id=user_id, state={})
        self.session.add(game)
        await self.session.commit()
        return game

    async def save_game(self, row: GuessGame, state: dict, status: str) -> None:
        row.state = state
        row.status = status
        if status != "active" and row.finished_at is None:
            row.finished_at = utcnow()
        await self.session.commit()

    async def remember_secret(self, row: GuessGame, name: str) -> None:
        row.secret = name[:128]
        await self.session.commit()

    # --- Пользователи ---------------------------------------------------
    async def upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
        *,
        subscribe_new: bool = False,
    ) -> User:
        user = await self.session.get(User, (self.tenant_id, user_id))
        if user is None:
            user = User(
                tenant_id=self.tenant_id,
                id=user_id,
                username=username,
                first_name=first_name,
                # Карта дня включается сразу: она открывает бесплатные расклады,
                # и человек, не знающий об условии, выглядит как отказавшийся
                daily_subscribed=subscribe_new,
            )
            self.session.add(user)
        else:
            user.username = username
            user.first_name = first_name
        await self.session.commit()
        return user

    async def get_user(self, user_id: int) -> User | None:
        return await self.session.get(User, (self.tenant_id, user_id))

    async def save_profile(self, user_id: int, **fields: object) -> User:
        """Записывает то, что человек рассказал о себе. Пустое не затирает."""
        user = await self.session.get(User, (self.tenant_id, user_id))
        if user is None:
            user = User(tenant_id=self.tenant_id, id=user_id)
            self.session.add(user)
        for name, value in fields.items():
            if value is not None:
                setattr(user, name, value)
        await self.session.commit()
        return user

    # --- Приглашения и промокоды ----------------------------------------
    async def add_bonus(self, user_id: int, readings: int) -> int:
        """Начисляет бесплатные расклады сверх обычных. Возвращает остаток."""
        user = await self.session.get(User, (self.tenant_id, user_id))
        if user is None:
            user = User(tenant_id=self.tenant_id, id=user_id)
            self.session.add(user)
        user.bonus_readings = (user.bonus_readings or 0) + readings
        await self.session.commit()
        return user.bonus_readings

    async def remember_inviter(self, user_id: int, inviter_id: int) -> bool:
        """Запоминает, кто привёл человека. Второй раз — уже нет.

        Пригласивший проставляется один раз и навсегда: иначе достаточно
        перезайти по чужой ссылке, чтобы выписать награду заново.
        """
        user = await self.session.get(User, (self.tenant_id, user_id))
        if user is None or user.invited_by is not None:
            return False
        user.invited_by = inviter_id
        await self.session.commit()
        return True

    async def invited_count(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(User)
            .where(User.tenant_id == self.tenant_id, User.invited_by == user_id)
        )
        return int(result.scalar_one())

    async def invited_summary(self, user_id: int) -> dict[str, int]:
        """Докуда дошли приглашённые этим человеком.

        Счёт приглашённых сам по себе мало что говорит: десять пришедших и
        ни одного расклада — это не десять клиентов. Поэтому считаем шаги.
        """
        guests = await self.session.execute(
            select(User.id).where(
                User.tenant_id == self.tenant_id, User.invited_by == user_id
            )
        )
        ids = [row for (row,) in guests.all()]
        if not ids:
            return {"invited": 0, "subscribed": 0, "with_reading": 0, "paid": 0}

        subscribed = await self.session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == self.tenant_id,
                User.id.in_(ids),
                User.daily_subscribed.is_(True),
            )
        )
        with_reading = await self.session.scalar(
            select(func.count(func.distinct(Reading.user_id))).where(
                Reading.tenant_id == self.tenant_id,
                Reading.user_id.in_(ids),
                Reading.mode == ReadingMode.AUTO,
            )
        )
        paid = await self.session.scalar(
            select(func.count(func.distinct(Payment.user_id))).where(
                Payment.tenant_id == self.tenant_id, Payment.user_id.in_(ids)
            )
        )
        return {
            "invited": len(ids),
            "subscribed": int(subscribed or 0),
            "with_reading": int(with_reading or 0),
            "paid": int(paid or 0),
        }

    async def inviters(self, limit: int = 20) -> list[dict[str, object]]:
        """Кто сколько привёл — для хозяина, свежие сверху по числу гостей."""
        counted = await self.session.execute(
            select(User.invited_by, func.count())
            .where(User.tenant_id == self.tenant_id, User.invited_by.is_not(None))
            .group_by(User.invited_by)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = []
        for inviter_id, count in counted.all():
            host = await self.session.get(User, (self.tenant_id, inviter_id))
            rows.append(
                {
                    "id": inviter_id,
                    "name": host.first_name if host else None,
                    "username": host.username if host else None,
                    "bonus": (host.bonus_readings or 0) if host else 0,
                    "invited": int(count),
                    **{
                        key: value
                        for key, value in (await self.invited_summary(inviter_id)).items()
                        if key != "invited"
                    },
                }
            )
        return rows

    async def promo_by_code(self, code: str) -> PromoCode | None:
        result = await self.session.execute(
            select(PromoCode).where(
                PromoCode.tenant_id == self.tenant_id, PromoCode.code == code.upper()
            )
        )
        return result.scalar_one_or_none()

    async def promo_used_by(self, code_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(PromoUse)
            .where(
                PromoUse.tenant_id == self.tenant_id,
                PromoUse.code_id == code_id,
                PromoUse.user_id == user_id,
            )
        )
        return bool(result.scalar_one())

    async def take_promo(self, promo: PromoCode, user_id: int) -> PromoUse:
        """Отмечает код использованным и начисляет расклады — одним заходом."""
        use = PromoUse(
            tenant_id=self.tenant_id,
            code_id=promo.id,
            user_id=user_id,
            readings=promo.readings,
        )
        self.session.add(use)
        promo.used += 1
        await self.session.commit()
        await self.add_bonus(user_id, promo.readings)
        return use

    async def add_promo(
        self,
        code: str,
        readings: int,
        uses_limit: int = 0,
        expires_at: date | None = None,
        note: str | None = None,
    ) -> PromoCode:
        promo = PromoCode(
            tenant_id=self.tenant_id,
            code=code.upper(),
            readings=readings,
            uses_limit=uses_limit,
            expires_at=expires_at,
            note=note,
        )
        self.session.add(promo)
        await self.session.commit()
        return promo

    async def promos(self, limit: int = 30) -> list[PromoCode]:
        result = await self.session.execute(
            select(PromoCode)
            .where(PromoCode.tenant_id == self.tenant_id)
            .order_by(PromoCode.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def set_promo_active(self, promo: PromoCode, active: bool) -> None:
        promo.active = active
        await self.session.commit()

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

    async def last_natal(self, user_id: int) -> Reading | None:
        """Написанный когда-то разбор карты: карта не меняется, и он тоже."""
        return await self.session.scalar(
            select(Reading)
            .where(
                Reading.tenant_id == self.tenant_id,
                Reading.user_id == user_id,
                Reading.mode == ReadingMode.NATAL,
            )
            .order_by(Reading.created_at.desc())
            .limit(1)
        )

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

    async def last_orders(self, limit: int = 20) -> list[Order]:
        """Последние заявки любого состояния — для стола заявок."""
        result = await self.session.execute(
            select(Order)
            .where(Order.tenant_id == self.tenant_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

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

    async def add_order_message(self, order_id: int, author: str, text: str) -> OrderMessage:
        """Записывает реплику. Пустой текст тоже реплика: ответ мог быть голосом."""
        message = OrderMessage(
            tenant_id=self.tenant_id, order_id=order_id, author=author, text=text
        )
        self.session.add(message)
        await self.session.commit()
        return message

    async def order_messages(self, order_id: int) -> list[OrderMessage]:
        result = await self.session.execute(
            select(OrderMessage)
            .where(
                OrderMessage.tenant_id == self.tenant_id,
                OrderMessage.order_id == order_id,
            )
            .order_by(OrderMessage.created_at, OrderMessage.id)
        )
        return list(result.scalars())

    async def orders_of(self, user_id: int, limit: int = 10) -> list[Order]:
        """Заявки человека, свежие сверху."""
        result = await self.session.execute(
            select(Order)
            .where(Order.tenant_id == self.tenant_id, Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

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

    async def nudge_candidates(
        self, *, older_than: datetime, quiet_until: datetime, limit: int, max_nudges: int
    ) -> list[User]:
        """Кто зашёл, но не подписался, и кому давно (или ещё не) напоминали.

        Условия складываются так, чтобы напоминание не превратилось в докучание:
        человек в боте не первый час, не подписан, напоминаний получил меньше
        предела, и с прошлого прошло достаточно времени.
        """
        result = await self.session.execute(
            select(User)
            .where(
                User.tenant_id == self.tenant_id,
                User.daily_subscribed.is_(False),
                User.created_at < older_than,
                User.nudges < max_nudges,
                or_(User.nudged_at.is_(None), User.nudged_at < quiet_until),
            )
            .order_by(User.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def mark_nudged(self, user: User, moment: datetime) -> None:
        user.nudged_at = moment
        user.nudges = (user.nudges or 0) + 1
        await self.session.commit()

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
        # Откуда люди и чем за них заплачено — вопрос владельца, а не клиента
        invited = int(
            await self.session.scalar(count(User).where(User.invited_by.is_not(None))) or 0
        )
        promo_used = int(await self.session.scalar(count(PromoUse)) or 0)
        return {
            "users": users,
            "readings": readings,
            "orders": orders,
            "open_orders": open_orders,
            "invited": invited,
            "promo_used": promo_used,
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
