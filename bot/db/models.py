"""Модели данных: пользователи, расклады и заявки на авторский разбор."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ReadingMode(str, enum.Enum):
    AUTO = "auto"
    AUTHOR = "author"
    # Разбор натальной карты: не расклад, и в счёт бесплатных не идёт
    NATAL = "natal"


class OrderStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    ANSWERED = "answered"
    DECLINED = "declined"
    CANCELLED = "cancelled"


ORDER_STATUS_LABELS: dict[OrderStatus, str] = {
    OrderStatus.NEW: "🆕 Новая",
    OrderStatus.IN_PROGRESS: "✍️ В работе",
    OrderStatus.ANSWERED: "✅ Отвечена",
    OrderStatus.DECLINED: "🚫 Отклонена",
    OrderStatus.CANCELLED: "↩️ Отменена клиентом",
}


class User(Base):
    __tablename__ = "users"

    # Один и тот же человек может писать ботам разных брендов: для каждого
    # он отдельный клиент со своей подпиской и своей историей
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Подписка на утреннюю карту дня
    daily_subscribed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", index=True
    )

    # Личный кабинет. Всё необязательно: человек заполняет, сколько захочет,
    # а разбор становится подробнее ровно настолько, насколько тот открылся.
    birth_date: Mapped[date | None] = mapped_column(Date)
    # Время хранится строкой «ЧЧ:ММ»: часового пояса рождения мы не знаем,
    # а приводить к UTC без него значит врать точностью
    birth_time: Mapped[str | None] = mapped_column(String(5))
    birth_city: Mapped[str | None] = mapped_column(String(64))
    # Координаты и пояс выбранного города: справочник со временем
    # меняется, а небо человека меняться не должно
    birth_lat: Mapped[float | None] = mapped_column(Float)
    birth_lon: Mapped[float | None] = mapped_column(Float)
    birth_tz: Mapped[str | None] = mapped_column(String(48))
    city: Mapped[str | None] = mapped_column(String(64))

    # Когда и сколько раз напоминали про карту дня. Молчание — тоже
    # обещание: напомнить пару раз можно, долбиться каждый день нельзя
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudges: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Кто привёл человека в бота. Ставится один раз, при первом входе по
    # ссылке: позже «вспомнить» пригласившего нельзя — иначе награду можно
    # выписать себе задним числом
    invited_by: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # Расклады сверх обычных бесплатных: за приглашённых и за промокоды.
    # Хранится числом, а не историей начислений: история есть в событиях,
    # а доступу нужен остаток
    bonus_readings: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    @property
    def display(self) -> str:
        name = self.first_name or "без имени"
        return f"{name} (@{self.username})" if self.username else name


class Reading(Base):
    """Сохранённый автоматический расклад."""

    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mode: Mapped[ReadingMode] = mapped_column(Enum(ReadingMode), default=ReadingMode.AUTO)
    spread_key: Mapped[str] = mapped_column(String(32))
    question: Mapped[str | None] = mapped_column(Text)
    cards: Mapped[list[dict]] = mapped_column(JSON)
    interpretation: Mapped[str | None] = mapped_column(Text)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class CardImage(Base):
    """Кэш картинок: id карты → file_id в Telegram, чтобы не ходить к источнику дважды."""

    __tablename__ = "card_images"

    # file_id выдаётся Telegram в привязке к боту: кэш чужого бота бесполезен
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    card_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    """Заявка на авторский разбор: карты раскладывает и разбирает человек, а не бот."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    question: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(64))
    # Уровень разбора на момент заявки: short | base | full
    tier: Mapped[str | None] = mapped_column(String(16))
    contact: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.NEW, index=True
    )
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # Сколько уточняющих вопросов человек уже задал по этой заявке
    followups_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS[self.status]

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.NEW, OrderStatus.IN_PROGRESS)


class Event(Base):
    """Шаг воронки: кто и когда дошёл до очередного этапа.

    Считаем именно шаги, а не нажатия: один человек может открыть авторский разбор
    трижды, но в воронке он один.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    # Подробность шага: уровень разбора, ключ расклада — что уместно
    detail: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class DailyCard(Base):
    """Карта дня: одна на человека в сутки.

    Хранится, чтобы карта не менялась при каждом открытии — иначе «карта дня»
    превращается в кнопку «вытянуть ещё раз».
    """

    __tablename__ = "daily_cards"
    # Карта дня у каждого бренда своя: один и тот же человек получает её
    # в каждом боте, на который подписан
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "day", name="uq_daily_card_user_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    card_id: Mapped[str] = mapped_column(String(32))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Отправлена ли она утренней рассылкой (а не открыта человеком самому)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    """Оплаченный авто-расклад.

    Хранится не баланс, а сами платежи: по ним видно, сколько и когда занесли,
    а остаток считается как «оплачено минус потрачено». Деньги приходят на счёт
    франчайзи, в базе остаётся только след.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    # За что заплачено: расклад и разбор карты — разные товары, и оплата
    # одного не должна открывать другое
    kind: Mapped[str] = mapped_column(String(32), default="auto_reading", index=True)
    charge_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    # Возвращённый платёж не открывает ничего: иначе возврат стал бы подарком
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    # Проставляется, когда по этому платежу сделан расклад
    spent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class OrderMessage(Base):
    """Реплика в переписке по заявке: ответ таролога или уточнение клиента.

    Ответ раньше просто пересылался человеку в чат и нигде не оставался —
    показать его в приложении было нечем, хотя платят именно за него. Теперь
    переписка хранится: чат остаётся способом доставки, а не единственным
    местом, где ответ существует.
    """

    __tablename__ = "order_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    # Кто сказал: author — таролог, client — человек с уточнением
    author: Mapped[str] = mapped_column(String(16), default="author")
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PromoCode(Base):
    """Промокод: несколько бесплатных раскладов тому, кто его ввёл.

    Код принадлежит арендатору: у двух франчайзи может быть одинаковое слово
    с разной щедростью, и чужой код в своём боте не работает.
    """

    __tablename__ = "promo_codes"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_promo_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    # Хранится в верхнем регистре: человек наберёт как придётся
    code: Mapped[str] = mapped_column(String(32), index=True)
    readings: Mapped[int] = mapped_column(Integer, default=1)
    # Ноль — без ограничения: не «кончился», а «не считаем»
    uses_limit: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str | None] = mapped_column(String(128))


class PromoUse(Base):
    """След того, что код уже использован этим человеком.

    Нужен не для отчётов, а для запрета: без него один код вводят столько раз,
    сколько хватит терпения.
    """

    __tablename__ = "promo_uses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code_id", "user_id", name="uq_promo_use"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    code_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    readings: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tenant(Base):
    """Бренд, работающий на этом сервисе: один арендатор — один бот.

    Франчайзи получает собственного бота под своим именем, но код, сервер и
    база остаются общими. Всё, чем один бренд отличается от другого, лежит
    здесь: токен, имя, цены. Имя и аватарку бота владелец
    ставит у BotFather сам — туда сервис не лезет.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(128))

    bot_token: Mapped[str] = mapped_column(String(64), unique=True)
    # Числовая часть токена: по ней обновление сопоставляется с арендатором
    bot_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    admin_chat_id: Mapped[int] = mapped_column(BigInteger, default=0)
    admin_ids: Mapped[list[int]] = mapped_column(JSON, default=list)

    author_name: Mapped[str] = mapped_column(String(64), default="таролог")
    currency: Mapped[str] = mapped_column(String(8), default="₽")

    price_short: Mapped[int] = mapped_column(Integer, default=500)
    price_base: Mapped[int] = mapped_column(Integer, default=700)
    price_full: Mapped[int] = mapped_column(Integer, default=1000)

    free_auto_readings: Mapped[int] = mapped_column(Integer, default=5)
    free_needs_daily: Mapped[bool] = mapped_column(Boolean, default=True)
    # Сколько бесплатных остаётся без подписки и сколько можно за сутки
    free_without_daily: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    free_daily_limit: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    # Цены в звёздах: платят только ими. Деньги идут на звёздный баланс
    # владельца бота, то есть самого арендатора
    auto_reading_stars: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    natal_reading_stars: Mapped[int] = mapped_column(Integer, default=150, server_default="150")

    miniapp_url: Mapped[str] = mapped_column(String(256), default="")

    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
