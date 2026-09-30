"""Все таблицы. После правки: make migration m="что поменял"."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Пользователь: email + пароль (хеш scrypt в app/auth.py)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        # GIN pg_trgm: быстрый ILIKE '%...%' по title (замена Elasticsearch в MVP)
        Index(
            "ix_items_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # server_default обязателен для NOT NULL поля в уже заполненной таблице
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """Очередь фоновых задач (замена Kafka/RabbitMQ). Работает с ней app/infra/queue.py."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "run_at", postgresql_where=text("status = 'new'")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        CheckConstraint("follower_id <> followee_id", name="different_users"),
        Index("ix_follows_followee_id", "followee_id", "follower_id"),
    )

    follower_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    followee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint("status IN ('created', 'published', 'deleted')", name="valid_status"),
        Index(
            "ix_posts_user_created",
            "user_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    image_key: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="created", server_default="created", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostsOutbox(Base):
    __tablename__ = "posts_outbox"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('CREATED POST', 'UPDATED POST', 'DELETED POST')", name="valid_event"
        ),
        Index("ix_posts_outbox_pending", "id", postgresql_where=text("processed_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)


class FeedEntry(Base):
    __tablename__ = "feed_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "post_id"),
        Index(
            "ix_feed_entries_user_created", "user_id", text("created_at DESC"), text("post_id DESC")
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
