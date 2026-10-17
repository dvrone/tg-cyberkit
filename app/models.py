"""Database models: Chat, User, ChatMember, Message, UsernameHistory.

Rule: `telegram_id` is the ID from Telegram, `id` is our own database key.
Never mix them. Foreign keys always point to the internal `id`.
All datetimes are stored in UTC.
"""

from datetime import datetime, timezone

from sqlalchemy import (BigInteger, DateTime, ForeignKey, Index, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.extensions import db


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime that is always stored as UTC and returned as aware UTC.

    SQLite has no timezone support, so we normalise manually.
    Naive datetimes are rejected to catch timezone bugs early.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed; use an aware datetime.")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class TimestampMixin:
    """Adds created_at / updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Chat(TimestampMixin, db.Model):
    """A Telegram group/channel observed by the collector."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))

    members: Mapped[list["ChatMember"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", passive_deletes=True
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Chat id={self.id} telegram_id={self.telegram_id}>"


class User(TimestampMixin, db.Model):
    """A Telegram account. Same telegram_id = same account (not proof of same person)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))

    memberships: Mapped[list["ChatMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    username_history: Mapped[list["UsernameHistory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id}>"


class UsernameHistory(db.Model):
    """Usernames the collector has observed for a user.

    first_observed / last_observed are times when OUR collector saw this
    username. They are not the times the user really changed it in Telegram.
    Usernames are stored normalised: lowercase, without '@'.
    """

    __tablename__ = "user_username_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    first_observed: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_observed: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    user: Mapped["User"] = relationship(back_populates="username_history")

    __table_args__ = (
        UniqueConstraint("user_id", "username", name="uq_username_history_user_name"),
        # Lets us search "who has ever used this username?" quickly.
        Index("ix_username_history_username", "username"),
    )

    def __repr__(self) -> str:
        return f"<UsernameHistory user_id={self.user_id} username={self.username!r}>"


class ChatMember(db.Model):
    """Many-to-many link between users and chats.

    first_seen / last_seen come from messages the collector saw.
    They are NOT the real join/leave times in Telegram.
    """

    __tablename__ = "chat_members"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    chat: Mapped["Chat"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")

    # The primary key index starts with chat_id; this one speeds up
    # "in which chats was this user seen?" lookups.
    __table_args__ = (Index("ix_chat_members_user_id", "user_id"),)


class Message(db.Model):
    """A Telegram message. Unique per chat by (chat_id, telegram_id)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    text: Mapped[str | None] = mapped_column(Text)
    date: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Telegram message ID of the replied-to message (NOT a database id).
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    user: Mapped["User | None"] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("chat_id", "telegram_id", name="uq_messages_chat_telegram"),
        Index("ix_messages_chat_date", "chat_id", "date"),
        Index("ix_messages_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} chat_id={self.chat_id} telegram_id={self.telegram_id}>"
