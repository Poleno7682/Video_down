from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import DownloadRequest, DownloadStatus, TelegramFileType, User, UserCookies, UserGoogleToken, Video
from app.db.utils import utcnow

# Single source of truth for "request is still in flight" states.
ACTIVE_STATUSES = (
    DownloadStatus.queued,
    DownloadStatus.downloading,
    DownloadStatus.sending,
)


class UserRepository:
    """Manages User entity persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> User:
        stmt = (
            insert(User)
            .values(id=user_id, username=username, first_name=first_name, updated_at=utcnow())
            .on_conflict_do_update(
                index_elements=[User.id],
                set_={"username": username, "first_name": first_name, "updated_at": utcnow()},
            )
            .returning(User)
        )
        user = self.session.execute(stmt).scalar_one()
        self.session.commit()
        return user

    def get_user(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def ban_user(self, user_id: int, seconds: int) -> None:
        user = self.session.get(User, user_id)
        if not user:
            user = User(id=user_id)
        user.is_banned = True
        user.banned_until = utcnow() + timedelta(seconds=seconds)
        self.session.add(user)
        self.session.commit()

    def unban_if_expired(self, user_id: int) -> bool:
        user = self.session.get(User, user_id)
        if user and user.is_banned and user.banned_until and user.banned_until <= utcnow():
            user.is_banned = False
            user.banned_until = None
            self.session.commit()
            return True
        return False

    def get_all_user_ids(self) -> list[int]:
        """Every user that ever interacted with the bot — used for broadcasts."""
        return list(self.session.execute(select(User.id)).scalars().all())


class CookieRepository:
    """Manages per-user yt-dlp cookies persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def set_user_cookies(self, user_id: int, platform: str, cookies_text: str) -> None:
        stmt = (
            insert(UserCookies)
            .values(
                user_id=user_id,
                platform=platform,
                cookies_text=cookies_text,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_update(
                constraint="uq_user_cookies_user_platform",
                set_={"cookies_text": cookies_text, "updated_at": utcnow()},
            )
        )
        self.session.execute(stmt)
        self.session.commit()

    def get_user_cookies(self, user_id: int, platform: str) -> str | None:
        stmt = select(UserCookies.cookies_text).where(
            UserCookies.user_id == user_id,
            UserCookies.platform == platform,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def delete_user_cookies(self, user_id: int, platform: str) -> bool:
        cookie = self.session.execute(
            select(UserCookies).where(
                UserCookies.user_id == user_id,
                UserCookies.platform == platform,
            )
        ).scalar_one_or_none()
        if not cookie:
            return False
        self.session.delete(cookie)
        self.session.commit()
        return True

    def list_user_platforms(self, user_id: int) -> list[str]:
        stmt = select(UserCookies.platform).where(UserCookies.user_id == user_id)
        return list(self.session.execute(stmt).scalars().all())


class GoogleTokenRepository:
    """Manages Google OAuth2 refresh tokens for YouTube cookie renewal."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def set_google_token(self, user_id: int, refresh_token: str) -> None:
        stmt = (
            insert(UserGoogleToken)
            .values(user_id=user_id, refresh_token=refresh_token, created_at=utcnow(), updated_at=utcnow())
            .on_conflict_do_update(
                constraint="uq_google_token_user",
                set_={"refresh_token": refresh_token, "updated_at": utcnow()},
            )
        )
        self.session.execute(stmt)
        self.session.commit()

    def get_google_token(self, user_id: int) -> UserGoogleToken | None:
        stmt = select(UserGoogleToken).where(UserGoogleToken.user_id == user_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def delete_google_token(self, user_id: int) -> bool:
        rec = self.session.execute(
            select(UserGoogleToken).where(UserGoogleToken.user_id == user_id)
        ).scalar_one_or_none()
        if not rec:
            return False
        self.session.delete(rec)
        self.session.commit()
        return True


class VideoRepository:
    """Manages Video entity persistence and cache invalidation."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_ready_video(self, url_hash: str, quality: str) -> Video | None:
        stmt = select(Video).where(
            Video.url_hash == url_hash,
            Video.quality == quality,
            Video.is_ready.is_(True),
            Video.telegram_file_id.is_not(None),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_or_create_video(
        self,
        original_url: str,
        normalized_url: str,
        url_hash: str,
        quality: str,
    ) -> Video:
        stmt = (
            insert(Video)
            .values(
                original_url=original_url,
                normalized_url=normalized_url,
                url_hash=url_hash,
                quality=quality,
                is_ready=False,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_update(
                constraint="uq_video_url_quality",
                set_={"updated_at": utcnow()},
            )
            .returning(Video)
        )
        video = self.session.execute(stmt).scalar_one()
        self.session.commit()
        return video

    def mark_video_ready(
        self,
        video_id: int,
        title: str | None,
        telegram_file_id: str,
        telegram_file_unique_id: str | None,
        telegram_file_type: TelegramFileType,
        local_file_path: str | None,
        file_size_bytes: int | None,
    ) -> None:
        video = self.session.get(Video, video_id)
        if not video:
            return
        video.title = title
        video.telegram_file_id = telegram_file_id
        video.telegram_file_unique_id = telegram_file_unique_id
        video.telegram_file_type = telegram_file_type
        video.local_file_path = local_file_path
        video.file_size_bytes = file_size_bytes
        video.is_ready = True
        video.last_error = None
        video.updated_at = utcnow()
        self.session.commit()

    def mark_video_failed(self, video_id: int, error: str) -> None:
        video = self.session.get(Video, video_id)
        if not video:
            return
        video.last_error = error
        video.updated_at = utcnow()
        self.session.commit()

    def invalidate_video_cache(self, video_id: int) -> None:
        """Clear cached Telegram file_id so the video will be re-downloaded."""
        video = self.session.get(Video, video_id)
        if not video:
            return
        video.is_ready = False
        video.telegram_file_id = None
        video.telegram_file_unique_id = None
        video.telegram_file_type = None
        video.updated_at = utcnow()
        self.session.commit()


class RequestRepository:
    """Manages DownloadRequest lifecycle and queue statistics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_request(
        self,
        user_id: int,
        chat_id: int,
        message_id: int | None,
        status_message_id: int | None,
        video_id: int | None,
        original_url: str,
        normalized_url: str,
        url_hash: str,
        quality: str,
        status: DownloadStatus = DownloadStatus.queued,
    ) -> DownloadRequest:
        req = DownloadRequest(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            status_message_id=status_message_id,
            video_id=video_id,
            original_url=original_url,
            normalized_url=normalized_url,
            url_hash=url_hash,
            quality=quality,
            status=status,
        )
        self.session.add(req)
        self.session.commit()
        self.session.refresh(req)
        return req

    def get_request(self, request_id: int) -> DownloadRequest | None:
        return self.session.get(DownloadRequest, request_id)

    def set_request_task_id(self, request_id: int, task_id: str) -> None:
        req = self.session.get(DownloadRequest, request_id)
        if not req:
            return
        req.celery_task_id = task_id
        self.session.commit()

    def update_request_status(
        self,
        request_id: int,
        status: DownloadStatus,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        req = self.session.get(DownloadRequest, request_id)
        if not req:
            return
        req.status = status
        req.error = error
        req.updated_at = utcnow()
        if finished:
            req.finished_at = utcnow()
        self.session.commit()

    def count_user_active_requests(self, user_id: int) -> int:
        stmt = select(func.count()).select_from(DownloadRequest).where(
            DownloadRequest.user_id == user_id,
            DownloadRequest.status.in_(ACTIVE_STATUSES),
        )
        return int(self.session.execute(stmt).scalar_one())

    def count_global_active_requests(self) -> int:
        stmt = select(func.count()).select_from(DownloadRequest).where(
            DownloadRequest.status.in_(ACTIVE_STATUSES),
        )
        return int(self.session.execute(stmt).scalar_one())

    def count_user_today_requests(self, user_id: int) -> int:
        start = utcnow() - timedelta(hours=24)
        stmt = select(func.count()).select_from(DownloadRequest).where(
            DownloadRequest.user_id == user_id,
            DownloadRequest.created_at >= start,
        )
        return int(self.session.execute(stmt).scalar_one())

    def has_active_video_job(self, url_hash: str, quality: str) -> bool:
        stmt = select(func.count()).select_from(DownloadRequest).where(
            DownloadRequest.url_hash == url_hash,
            DownloadRequest.quality == quality,
            DownloadRequest.status.in_(ACTIVE_STATUSES),
        )
        return int(self.session.execute(stmt).scalar_one()) > 0


class Repository(UserRepository, CookieRepository, GoogleTokenRepository, VideoRepository, RequestRepository):
    """Unified facade over all domain repositories — preserves existing call sites."""

    def __init__(self, session: Session) -> None:
        self.session = session
