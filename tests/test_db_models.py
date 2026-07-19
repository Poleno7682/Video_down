from app.db.models import DownloadRequest, DownloadStatus, Proxy, TelegramFileType, User, Video


def test_download_status_values():
    assert DownloadStatus.queued == "queued"
    assert DownloadStatus.downloading == "downloading"
    assert DownloadStatus.sending == "sending"
    assert DownloadStatus.done == "done"
    assert DownloadStatus.failed == "failed"
    assert DownloadStatus.too_large == "too_large"
    assert DownloadStatus.rate_limited == "rate_limited"


def test_telegram_file_type_values():
    assert TelegramFileType.video == "video"
    assert TelegramFileType.audio == "audio"
    assert TelegramFileType.document == "document"


def test_user_instantiation():
    u = User(id=1, username="alice", first_name="Alice", is_banned=False)
    assert u.id == 1
    assert u.username == "alice"
    assert u.first_name == "Alice"
    assert u.is_banned is False


def test_proxy_instantiation():
    p = Proxy(id=1, url="socks5h://user:pass@host:1080", failure_count=0, added_by=42)
    assert p.id == 1
    assert p.url == "socks5h://user:pass@host:1080"
    assert p.failure_count == 0
    assert p.added_by == 42


def test_video_instantiation():
    v = Video(
        original_url="https://example.com/v",
        normalized_url="https://example.com/v",
        url_hash="abc123",
        quality="720p",
        is_ready=False,
    )
    assert v.url_hash == "abc123"
    assert v.quality == "720p"
    assert v.is_ready is False


def test_download_request_instantiation():
    r = DownloadRequest(
        user_id=1,
        chat_id=2,
        original_url="https://example.com",
        normalized_url="https://example.com",
        url_hash="h",
        quality="720p",
        status=DownloadStatus.queued,
    )
    assert r.user_id == 1
    assert r.status == DownloadStatus.queued
