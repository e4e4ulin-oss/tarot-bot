"""Модели данных: пользователи, расклады и заявки на авторский разбор."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ReadingMode(str, enum.Enum):
    AUTO = "auto"
    AUTHOR = "author"


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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    readings: Mapped[list[Reading]] = relationship(back_populates="user")
    orders: Mapped[list[Order]] = relationship(back_populates="user")

    @property
    def display(self) -> str:
        name = self.first_name or "без имени"
        return f"{name} (@{self.username})" if self.username else name


class Reading(Base):
    """Сохранённый автоматический расклад."""

    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    mode: Mapped[ReadingMode] = mapped_column(Enum(ReadingMode), default=ReadingMode.AUTO)
    spread_key: Mapped[str] = mapped_column(String(32))
    question: Mapped[str | None] = mapped_column(Text)
    cards: Mapped[list[dict]] = mapped_column(JSON)
    interpretation: Mapped[str | None] = mapped_column(Text)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    user: Mapped[User] = relationship(back_populates="readings")


class CardImage(Base):
    """Кэш картинок: id карты → file_id в Telegram, чтобы не ходить к источнику дважды."""

    __tablename__ = "card_images"

    card_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    """Заявка на авторский разбор: Анастасия раскладывает и разбирает сама."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(64))
    contact: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.NEW, index=True
    )
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="orders")

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS[self.status]

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.NEW, OrderStatus.IN_PROGRESS)
