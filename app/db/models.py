from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.utils import utcnow


class Base(DeclarativeBase):
    pass


class DownloadStatus(str, enum.Enum):
    queued = "queued"
    downloading = "downloading"
    sending = "sending"
    done = "done"
    failed = "failed"
    too_large = "too_large"
    rate_limited = "rate_limited"


class TelegramFileType(str, enum.Enum):
    video = "video"
    document = "document"
    audio = "audio"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UserCookies(Base):
    """Per-user yt-dlp cookies (Netscape format) for a given platform.

    Stored in the DB so that the bot (writer) and the worker (reader) — which run
    in separate containers — share the same source of truth without file mounts.
    The worker materializes a temporary cookiefile at download time.
    """

    __tablename__ = "user_cookies"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_user_cookies_user_platform"),
        Index("idx_user_cookies_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    cookies_text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("url_hash", "quality", name="uq_video_url_quality"),
        Index("idx_video_url_hash", "url_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    telegram_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_file_type: Mapped[TelegramFileType | None] = mapped_column(Enum(TelegramFileType), nullable=True)

    local_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    is_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class DownloadRequest(Base):
    __tablename__ = "download_requests"
    __table_args__ = (
        Index("idx_request_user_status", "user_id", "status"),
        Index("idx_request_video_status", "video_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    video_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("videos.id", ondelete="SET NULL", name="fk_dr_video_id"),
        nullable=True,
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[DownloadStatus] = mapped_column(Enum(DownloadStatus), default=DownloadStatus.queued, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
