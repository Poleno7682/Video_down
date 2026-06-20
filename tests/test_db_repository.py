from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import DownloadRequest, DownloadStatus, TelegramFileType, User, Video
from app.db.repository import (
    ACTIVE_STATUSES,
    Repository,
    RequestRepository,
    UserRepository,
    VideoRepository,
)


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session():
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# ACTIVE_STATUSES
# ---------------------------------------------------------------------------

def test_active_statuses_contains_expected():
    assert DownloadStatus.queued in ACTIVE_STATUSES
    assert DownloadStatus.downloading in ACTIVE_STATUSES
    assert DownloadStatus.sending in ACTIVE_STATUSES
    assert DownloadStatus.done not in ACTIVE_STATUSES
    assert DownloadStatus.failed not in ACTIVE_STATUSES


# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------

class TestUserRepository:
    def setup_method(self):
        self.session = _make_session()
        self.repo = UserRepository(self.session)

    def test_upsert_user_returns_user(self):
        mock_user = User(id=1)
        self.session.execute.return_value.scalar_one.return_value = mock_user
        result = self.repo.upsert_user(1, "alice", "Alice")
        assert result is mock_user
        self.session.commit.assert_called()

    def test_get_user_found(self):
        mock_user = User(id=1)
        self.session.get.return_value = mock_user
        assert self.repo.get_user(1) is mock_user

    def test_get_user_not_found(self):
        self.session.get.return_value = None
        assert self.repo.get_user(99) is None

    def test_ban_user_existing(self):
        mock_user = User(id=1, is_banned=False)
        self.session.get.return_value = mock_user
        self.repo.ban_user(1, 600)
        assert mock_user.is_banned is True
        assert mock_user.banned_until is not None
        self.session.commit.assert_called()

    def test_ban_user_creates_new_if_missing(self):
        self.session.get.return_value = None
        self.repo.ban_user(99, 300)
        self.session.add.assert_called_once()
        self.session.commit.assert_called()

    def test_unban_if_expired_unbans(self):
        past = utcnow() - timedelta(seconds=10)
        mock_user = User(id=1, is_banned=True, banned_until=past)
        self.session.get.return_value = mock_user
        result = self.repo.unban_if_expired(1)
        assert result is True
        assert mock_user.is_banned is False
        self.session.commit.assert_called()

    def test_unban_if_expired_still_banned(self):
        future = utcnow() + timedelta(seconds=300)
        mock_user = User(id=1, is_banned=True, banned_until=future)
        self.session.get.return_value = mock_user
        assert self.repo.unban_if_expired(1) is False

    def test_unban_if_expired_not_banned(self):
        mock_user = User(id=1, is_banned=False, banned_until=None)
        self.session.get.return_value = mock_user
        assert self.repo.unban_if_expired(1) is False

    def test_unban_if_expired_user_not_found(self):
        self.session.get.return_value = None
        assert self.repo.unban_if_expired(99) is False


# ---------------------------------------------------------------------------
# VideoRepository
# ---------------------------------------------------------------------------

class TestVideoRepository:
    def setup_method(self):
        self.session = _make_session()
        self.repo = VideoRepository(self.session)

    def test_get_ready_video_found(self):
        mock_video = Video(id=1, is_ready=True)
        self.session.execute.return_value.scalar_one_or_none.return_value = mock_video
        result = self.repo.get_ready_video("hash", "720p")
        assert result is mock_video

    def test_get_ready_video_not_found(self):
        self.session.execute.return_value.scalar_one_or_none.return_value = None
        assert self.repo.get_ready_video("hash", "720p") is None

    def test_get_or_create_video(self):
        mock_video = Video(id=1)
        self.session.execute.return_value.scalar_one.return_value = mock_video
        result = self.repo.get_or_create_video("http://x.com", "http://x.com", "h", "720p")
        assert result is mock_video
        self.session.commit.assert_called()

    def test_mark_video_ready(self):
        mock_video = Video(id=1, is_ready=False)
        self.session.get.return_value = mock_video
        self.repo.mark_video_ready(
            video_id=1,
            title="Test",
            telegram_file_id="fid",
            telegram_file_unique_id="uid",
            telegram_file_type=TelegramFileType.video,
            local_file_path="/tmp/file.mp4",
            file_size_bytes=1000,
        )
        assert mock_video.is_ready is True
        assert mock_video.telegram_file_id == "fid"
        self.session.commit.assert_called()

    def test_mark_video_ready_not_found(self):
        self.session.get.return_value = None
        # Should not raise
        self.repo.mark_video_ready(99, None, "fid", None, TelegramFileType.video, None, None)
        self.session.commit.assert_not_called()

    def test_mark_video_failed(self):
        mock_video = Video(id=1, last_error=None)
        self.session.get.return_value = mock_video
        self.repo.mark_video_failed(1, "some error")
        assert mock_video.last_error == "some error"
        self.session.commit.assert_called()

    def test_mark_video_failed_not_found(self):
        self.session.get.return_value = None
        self.repo.mark_video_failed(99, "err")
        self.session.commit.assert_not_called()

    def test_invalidate_video_cache(self):
        mock_video = Video(id=1, is_ready=True, telegram_file_id="fid")
        self.session.get.return_value = mock_video
        self.repo.invalidate_video_cache(1)
        assert mock_video.is_ready is False
        assert mock_video.telegram_file_id is None
        assert mock_video.telegram_file_type is None
        self.session.commit.assert_called()

    def test_invalidate_video_cache_not_found(self):
        self.session.get.return_value = None
        self.repo.invalidate_video_cache(99)
        self.session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# RequestRepository
# ---------------------------------------------------------------------------

class TestRequestRepository:
    def setup_method(self):
        self.session = _make_session()
        self.repo = RequestRepository(self.session)

    def test_create_request(self):
        mock_req = DownloadRequest(id=1)
        self.session.refresh = MagicMock()
        self.session.add = MagicMock()
        # After commit+refresh, the object is returned
        self.repo.session = self.session
        req = DownloadRequest(
            id=1, user_id=1, chat_id=2,
            original_url="u", normalized_url="u",
            url_hash="h", quality="720p",
            status=DownloadStatus.queued,
        )
        self.session.add.side_effect = lambda r: None
        # Test that create_request calls add/commit/refresh
        with patch.object(self.repo, "session") as ms:
            ms.add = MagicMock()
            ms.commit = MagicMock()
            ms.refresh = MagicMock()
            # create a real DownloadRequest
            ms.refresh.side_effect = lambda r: setattr(r, 'id', 1)
            result = self.repo.create_request(
                user_id=1, chat_id=2, message_id=3,
                status_message_id=4, video_id=5,
                original_url="u", normalized_url="u",
                url_hash="h", quality="720p",
            )
            ms.add.assert_called_once()
            ms.commit.assert_called_once()
            ms.refresh.assert_called_once()

    def test_get_request_found(self):
        mock_req = DownloadRequest(id=1)
        self.session.get.return_value = mock_req
        assert self.repo.get_request(1) is mock_req

    def test_get_request_not_found(self):
        self.session.get.return_value = None
        assert self.repo.get_request(99) is None

    def test_set_request_task_id(self):
        mock_req = DownloadRequest(id=1, celery_task_id=None)
        self.session.get.return_value = mock_req
        self.repo.set_request_task_id(1, "celery-task-uuid")
        assert mock_req.celery_task_id == "celery-task-uuid"
        self.session.commit.assert_called()

    def test_set_request_task_id_not_found(self):
        self.session.get.return_value = None
        self.repo.set_request_task_id(99, "tid")
        self.session.commit.assert_not_called()

    def test_update_request_status(self):
        mock_req = DownloadRequest(id=1, status=DownloadStatus.queued)
        self.session.get.return_value = mock_req
        self.repo.update_request_status(1, DownloadStatus.downloading)
        assert mock_req.status == DownloadStatus.downloading
        assert mock_req.finished_at is None
        self.session.commit.assert_called()

    def test_update_request_status_finished(self):
        mock_req = DownloadRequest(id=1, status=DownloadStatus.queued)
        self.session.get.return_value = mock_req
        self.repo.update_request_status(1, DownloadStatus.done, finished=True)
        assert mock_req.finished_at is not None

    def test_update_request_status_not_found(self):
        self.session.get.return_value = None
        self.repo.update_request_status(99, DownloadStatus.done)
        self.session.commit.assert_not_called()

    def test_count_user_active_requests(self):
        self.session.execute.return_value.scalar_one.return_value = 3
        count = self.repo.count_user_active_requests(1)
        assert count == 3

    def test_count_global_active_requests(self):
        self.session.execute.return_value.scalar_one.return_value = 10
        assert self.repo.count_global_active_requests() == 10

    def test_count_user_today_requests(self):
        self.session.execute.return_value.scalar_one.return_value = 5
        assert self.repo.count_user_today_requests(1) == 5

    def test_has_active_video_job_true(self):
        self.session.execute.return_value.scalar_one.return_value = 2
        assert self.repo.has_active_video_job("hash", "720p") is True

    def test_has_active_video_job_false(self):
        self.session.execute.return_value.scalar_one.return_value = 0
        assert self.repo.has_active_video_job("hash", "720p") is False


# ---------------------------------------------------------------------------
# Repository (facade)
# ---------------------------------------------------------------------------

class TestRepositoryFacade:
    def test_repository_has_all_methods(self):
        session = _make_session()
        repo = Repository(session)
        # Methods from all three sub-repos
        assert hasattr(repo, "upsert_user")
        assert hasattr(repo, "get_ready_video")
        assert hasattr(repo, "create_request")

    def test_repository_shares_session(self):
        session = _make_session()
        repo = Repository(session)
        assert repo.session is session
