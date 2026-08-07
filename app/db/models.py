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


# Этот проект живёт в БД, общей как минимум с одним другим, не связанным
# проектом (см. docs/db-table-namespacing.md). Любая таблица без этого
# суффикса рискует однажды столкнуться по имени с таблицей другого проекта.
TABLE_SUFFIX = "_video_bot"


class Base(DeclarativeBase):
    def __init_subclass__(cls, **kwargs) -> None:
        # Check before SQLAlchemy's own __init_subclass__ maps the class, so
        # a bad name fails before a Table object is ever registered into
        # Base.metadata.
        tablename = cls.__dict__.get("__tablename__")
        if tablename is not None and not tablename.endswith(TABLE_SUFFIX):
            raise ValueError(
                f"{cls.__name__}.__tablename__ = {tablename!r} must end with "
                f"the {TABLE_SUFFIX!r} suffix (see docs/db-table-namespacing.md)"
            )
        super().__init_subclass__(**kwargs)


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
    __tablename__ = "users_video_bot"

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

    __tablename__ = "user_cookies_video_bot"
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
    __tablename__ = "videos_video_bot"
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


class UserGoogleToken(Base):
    """Google OAuth2 refresh token for automatic YouTube cookie renewal."""

    __tablename__ = "user_google_tokens_video_bot"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_google_token_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Proxy(Base):
    """A SOCKS5(h) proxy yt-dlp can route requests through.

    Managed via Telegram admin commands and shared with the worker through
    the DB (same pattern as UserCookies) rather than a single static env var,
    so an admin can add/remove proxies without redeploying. failure_count
    drives failover ordering: proxies that keep failing sink to the back of
    the rotation instead of being tried first on every request.
    """

    __tablename__ = "proxies_video_bot"
    __table_args__ = (
        UniqueConstraint("url", name="uq_proxy_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TorrentRequest(Base):
    """One magnet-link download. Deliberately separate from Video/DownloadRequest:
    a torrent isn't "a video at a quality" — it's an arbitrary set of files with
    no caching/reuse concept, since the bot deletes everything right after
    sending it (see app.worker.tasks.process_torrent_request).
    """

    __tablename__ = "torrent_requests_video_bot"
    __table_args__ = (
        Index("idx_torrent_request_user_status", "user_id", "status"),
        Index("idx_torrent_request_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    magnet_uri: Mapped[str] = mapped_column(Text, nullable=False)
    info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[DownloadStatus] = mapped_column(Enum(DownloadStatus), default=DownloadStatus.queued, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DownloadRequest(Base):
    __tablename__ = "download_requests_video_bot"
    __table_args__ = (
        Index("idx_request_user_status", "user_id", "status"),
        Index("idx_request_video_status", "video_id", "status"),
        Index("idx_request_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    video_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("videos_video_bot.id", ondelete="SET NULL", name="fk_dr_video_id"),
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
