from __future__ import annotations

import pytest

from app.services.rate_limiter import RateLimiter


@pytest.fixture()
def limiter(mock_redis):
    mock_redis.register_script.return_value = mock_redis.register_script.return_value
    # register_script returns a callable mock; configure per-test
    return RateLimiter(mock_redis)


class TestIsBanned:
    def test_banned(self, mock_redis):
        mock_redis.register_script.return_value = mock_redis.register_script.return_value
        lim = RateLimiter(mock_redis)
        mock_redis.ttl.return_value = 300
        assert lim.is_banned(1) == 300

    def test_not_banned(self, mock_redis):
        lim = RateLimiter(mock_redis)
        mock_redis.ttl.return_value = -2
        assert lim.is_banned(1) == 0

    def test_zero_ttl_returns_zero(self, mock_redis):
        lim = RateLimiter(mock_redis)
        mock_redis.ttl.return_value = 0
        assert lim.is_banned(1) == 0


class TestBan:
    def test_ban_sets_key(self, mock_redis):
        lim = RateLimiter(mock_redis)
        lim.ban(42, 600)
        mock_redis.setex.assert_called_once_with("ban:user:42", 600, "1")


class TestHitOrBan:
    def test_allowed(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = [1, 0]
        lim = RateLimiter(mock_redis)
        allowed, ttl = lim.hit_or_ban(1, 60, 8, 600)
        assert allowed is True
        assert ttl == 0

    def test_banned_existing(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = [0, 350]
        lim = RateLimiter(mock_redis)
        allowed, ttl = lim.hit_or_ban(1, 60, 8, 600)
        assert allowed is False
        assert ttl == 350

    def test_newly_banned(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = [0, 600]
        lim = RateLimiter(mock_redis)
        allowed, ttl = lim.hit_or_ban(1, 60, 8, 600)
        assert allowed is False
        assert ttl == 600

    def test_calls_script_with_correct_keys(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = [1, 0]
        lim = RateLimiter(mock_redis)
        lim.hit_or_ban(99, 60, 5, 300)
        script.assert_called_once_with(
            keys=["ban:user:99", "rate:user:99"],
            args=[60, 5, 300],
        )


class TestAcquireUserDownloadSlot:
    def test_acquired(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = 1
        lim = RateLimiter(mock_redis)
        assert lim.acquire_user_download_slot(1, 2, 1800) is True

    def test_not_acquired(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = 0
        lim = RateLimiter(mock_redis)
        assert lim.acquire_user_download_slot(1, 2, 1800) is False

    def test_calls_script_with_correct_args(self, mock_redis):
        script = mock_redis.register_script.return_value
        script.return_value = 1
        lim = RateLimiter(mock_redis)
        lim.acquire_user_download_slot(7, 3, 900)
        script.assert_called_with(
            keys=["active_downloads:user:7"],
            args=[3, 900],
        )


class TestReleaseUserDownloadSlot:
    def test_decrements_when_positive(self, mock_redis):
        lim = RateLimiter(mock_redis)
        mock_redis.get.return_value = "2"
        lim.release_user_download_slot(1)
        mock_redis.decr.assert_called_once_with("active_downloads:user:1")

    def test_does_not_decrement_when_zero(self, mock_redis):
        lim = RateLimiter(mock_redis)
        mock_redis.get.return_value = "0"
        lim.release_user_download_slot(1)
        mock_redis.decr.assert_not_called()

    def test_does_not_decrement_when_none(self, mock_redis):
        lim = RateLimiter(mock_redis)
        mock_redis.get.return_value = None
        lim.release_user_download_slot(1)
        mock_redis.decr.assert_not_called()

    def test_swallows_exception(self, mock_redis):
        lim = RateLimiter(mock_redis)
        mock_redis.get.side_effect = ConnectionError("Redis down")
        # Should not raise
        lim.release_user_download_slot(1)


class TestVideoLock:
    def test_acquire_success(self, mock_redis):
        mock_redis.set.return_value = True
        lim = RateLimiter(mock_redis)
        assert lim.acquire_video_lock("hash", "720p", 1800) is True
        mock_redis.set.assert_called_once_with("lock:video:hash:720p", "1", ex=1800, nx=True)

    def test_acquire_failure(self, mock_redis):
        mock_redis.set.return_value = None
        lim = RateLimiter(mock_redis)
        assert lim.acquire_video_lock("hash", "720p", 1800) is False

    def test_release(self, mock_redis):
        lim = RateLimiter(mock_redis)
        lim.release_video_lock("hash", "720p")
        mock_redis.delete.assert_called_once_with("lock:video:hash:720p")
