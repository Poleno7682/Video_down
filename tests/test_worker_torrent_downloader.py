from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import libtorrent as lt
import pytest

from app.worker.torrent_downloader import (
    TorrentError,
    TorrentFile,
    TorrentTooLargeError,
    TorrentTooManyFilesError,
    _DHT_BOOTSTRAP_NODES,
    _enforce_torrent_limits,
    add_magnet,
    build_session,
    download_torrent,
    list_files,
    remove_and_delete,
    status_to_progress,
    wait_for_metadata,
)


def _settings(**overrides):
    s = MagicMock()
    s.torrent_listen_interfaces = "0.0.0.0:6881"
    s.torrent_upload_limit_kbps = 50
    s.torrent_max_total_size_mb = 2000
    s.torrent_max_file_count = 20
    s.torrent_metadata_timeout_seconds = 120
    s.torrent_max_duration_seconds = 3600
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# build_session
# ---------------------------------------------------------------------------

def test_build_session_applies_expected_settings():
    settings = _settings(torrent_listen_interfaces="0.0.0.0:7000", torrent_upload_limit_kbps=100)
    mock_session = MagicMock()
    with patch("app.worker.torrent_downloader.lt.session", return_value=mock_session):
        result = build_session(settings)

    assert result is mock_session
    applied = mock_session.apply_settings.call_args[0][0]
    assert applied["listen_interfaces"] == "0.0.0.0:7000"
    assert applied["enable_dht"] is True
    assert applied["enable_lsd"] is True
    assert applied["enable_upnp"] is False
    assert applied["enable_natpmp"] is False
    assert applied["download_rate_limit"] == 0
    assert applied["upload_rate_limit"] == 100 * 1024


def test_build_session_upload_limit_floors_at_1kbps():
    settings = _settings(torrent_upload_limit_kbps=0)
    mock_session = MagicMock()
    with patch("app.worker.torrent_downloader.lt.session", return_value=mock_session):
        build_session(settings)

    applied = mock_session.apply_settings.call_args[0][0]
    assert applied["upload_rate_limit"] == 1024


def test_build_session_registers_all_dht_bootstrap_nodes():
    settings = _settings()
    mock_session = MagicMock()
    with patch("app.worker.torrent_downloader.lt.session", return_value=mock_session):
        build_session(settings)

    calls = [call.args for call in mock_session.add_dht_router.call_args_list]
    assert calls == list(_DHT_BOOTSTRAP_NODES)


# ---------------------------------------------------------------------------
# add_magnet
# ---------------------------------------------------------------------------

def test_add_magnet_sets_save_path_and_adds_torrent():
    session = MagicMock()
    params = MagicMock()
    handle = MagicMock()
    session.add_torrent.return_value = handle

    with patch("app.worker.torrent_downloader.lt.parse_magnet_uri", return_value=params) as mock_parse:
        result = add_magnet(session, "magnet:?xt=urn:btih:abc", Path("/tmp/work"))

    mock_parse.assert_called_once_with("magnet:?xt=urn:btih:abc")
    assert params.save_path == "/tmp/work"
    session.add_torrent.assert_called_once_with(params)
    assert result is handle


# ---------------------------------------------------------------------------
# wait_for_metadata
# ---------------------------------------------------------------------------

def test_wait_for_metadata_returns_immediately_when_already_present():
    handle = MagicMock()
    handle.status.return_value.has_metadata = True

    with patch("app.worker.torrent_downloader.time.sleep") as mock_sleep:
        wait_for_metadata(handle, timeout_seconds=10)

    mock_sleep.assert_not_called()


def test_wait_for_metadata_polls_until_metadata_arrives():
    handle = MagicMock()
    handle.status.side_effect = [
        MagicMock(has_metadata=False),
        MagicMock(has_metadata=False),
        MagicMock(has_metadata=True),
    ]

    with patch("app.worker.torrent_downloader.time.sleep") as mock_sleep, \
         patch("app.worker.torrent_downloader.time.monotonic", side_effect=[0, 1, 2, 3]):
        wait_for_metadata(handle, timeout_seconds=100)

    assert mock_sleep.call_count == 2


def test_wait_for_metadata_raises_on_timeout():
    handle = MagicMock()
    handle.status.return_value.has_metadata = False

    with patch("app.worker.torrent_downloader.time.sleep") as mock_sleep, \
         patch("app.worker.torrent_downloader.time.monotonic", side_effect=[0, 100, 200]):
        with pytest.raises(TorrentError, match="Timed out"):
            wait_for_metadata(handle, timeout_seconds=10)

    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

def test_list_files_returns_torrent_files_from_storage():
    handle = MagicMock()
    storage = MagicMock()
    storage.num_files.return_value = 2
    storage.file_path.side_effect = ["movie.mkv", "subs/movie.srt"]
    storage.file_size.side_effect = [123456, 789]
    handle.torrent_file.return_value.files.return_value = storage

    result = list_files(handle)

    assert result == [
        TorrentFile(path=Path("movie.mkv"), size_bytes=123456),
        TorrentFile(path=Path("subs/movie.srt"), size_bytes=789),
    ]


def test_list_files_empty_torrent():
    handle = MagicMock()
    storage = MagicMock()
    storage.num_files.return_value = 0
    handle.torrent_file.return_value.files.return_value = storage

    assert list_files(handle) == []


# ---------------------------------------------------------------------------
# status_to_progress
# ---------------------------------------------------------------------------

def test_status_to_progress_maps_all_fields():
    status = MagicMock()
    status.state = 3
    status.progress = 0.5
    status.download_rate = 1000.0
    status.upload_rate = 200.0
    status.num_peers = 5
    status.num_seeds = 2
    status.num_incomplete = 10
    status.num_complete = 4
    status.total_wanted = 5000
    status.total_wanted_done = 2500

    progress = status_to_progress(status)

    assert progress.state == str(status.state)
    assert progress.progress == 0.5
    assert progress.download_rate == 1000.0
    assert progress.upload_rate == 200.0
    assert progress.peers_connected == 5
    assert progress.seeds_connected == 2
    assert progress.peers_available == 10
    assert progress.seeds_available == 4
    assert progress.total_size == 5000
    assert progress.downloaded == 2500


# ---------------------------------------------------------------------------
# _enforce_torrent_limits
# ---------------------------------------------------------------------------

def test_enforce_limits_passes_within_bounds():
    files = [TorrentFile(Path("a"), 1024 * 1024), TorrentFile(Path("b"), 1024 * 1024)]
    _enforce_torrent_limits(files, _settings(torrent_max_total_size_mb=10, torrent_max_file_count=5))


def test_enforce_limits_raises_when_total_size_too_large():
    files = [TorrentFile(Path("a"), 20 * 1024 * 1024)]
    with pytest.raises(TorrentTooLargeError):
        _enforce_torrent_limits(files, _settings(torrent_max_total_size_mb=10, torrent_max_file_count=5))


def test_enforce_limits_raises_when_too_many_files():
    files = [TorrentFile(Path(f"f{i}"), 1) for i in range(6)]
    with pytest.raises(TorrentTooManyFilesError):
        _enforce_torrent_limits(files, _settings(torrent_max_total_size_mb=1000, torrent_max_file_count=5))


def test_enforce_limits_zero_disables_size_check():
    files = [TorrentFile(Path("a"), 999 * 1024 * 1024 * 1024)]
    _enforce_torrent_limits(files, _settings(torrent_max_total_size_mb=0, torrent_max_file_count=5))


def test_enforce_limits_zero_disables_file_count_check():
    files = [TorrentFile(Path(f"f{i}"), 1) for i in range(100)]
    _enforce_torrent_limits(files, _settings(torrent_max_total_size_mb=1000, torrent_max_file_count=0))


def test_enforce_limits_error_message_includes_limit():
    files = [TorrentFile(Path("a"), 20 * 1024 * 1024)]
    with pytest.raises(TorrentTooLargeError, match="10 MB"):
        _enforce_torrent_limits(files, _settings(torrent_max_total_size_mb=10, torrent_max_file_count=5))


# ---------------------------------------------------------------------------
# download_torrent
# ---------------------------------------------------------------------------

def test_download_torrent_success_returns_files_and_invokes_hooks(tmp_path):
    settings = _settings(torrent_max_duration_seconds=60)
    settings.download_dir = tmp_path

    fixed_hex = "aabbccdd"
    mock_uuid = MagicMock(hex=fixed_hex)
    session = MagicMock()
    handle = MagicMock()
    files = [TorrentFile(Path("movie.mkv"), 100)]
    finished_status = MagicMock(info_hash="abc123", is_finished=True)
    finished_status.name = "MyTorrent"  # MagicMock(name=...) sets the mock's repr, not an attribute
    handle.status.return_value = finished_status

    work_dir = tmp_path / "torrents" / fixed_hex
    metadata_calls = []
    progress_calls = []

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch("app.worker.torrent_downloader.wait_for_metadata"), \
         patch("app.worker.torrent_downloader.list_files", return_value=files), \
         patch("app.worker.torrent_downloader._enforce_torrent_limits"):
        # download_torrent creates work_dir itself before add_magnet is called;
        # the downloaded file is written here to simulate a finished torrent.
        (tmp_path / "torrents" / fixed_hex).mkdir(parents=True, exist_ok=True)
        (work_dir / "movie.mkv").write_bytes(b"x" * 100)

        result_session, result_handle, result_work_dir, result_paths = download_torrent(
            "magnet:?xt=urn:btih:abc", settings,
            on_metadata=lambda *args: metadata_calls.append(args),
            on_progress=lambda p: progress_calls.append(p),
        )

    assert result_session is session
    assert result_handle is handle
    assert result_work_dir == work_dir
    assert result_paths == [work_dir / "movie.mkv"]
    assert metadata_calls == [("abc123", "MyTorrent", files)]
    assert len(progress_calls) == 1


def test_download_torrent_creates_its_own_work_dir(tmp_path):
    settings = _settings(torrent_max_duration_seconds=60)
    settings.download_dir = tmp_path

    mock_uuid = MagicMock(hex="workdirtest")
    session = MagicMock()
    handle = MagicMock()
    handle.status.return_value = MagicMock(info_hash="x", name="n", is_finished=True)

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch("app.worker.torrent_downloader.wait_for_metadata"), \
         patch("app.worker.torrent_downloader.list_files", return_value=[]), \
         patch("app.worker.torrent_downloader._enforce_torrent_limits"):
        _, _, work_dir, file_paths = download_torrent("magnet:?xt=urn:btih:abc", settings)

    assert work_dir == tmp_path / "torrents" / "workdirtest"
    assert work_dir.is_dir()
    assert file_paths == []


def test_download_torrent_cleans_up_and_reraises_on_size_limit_error(tmp_path):
    settings = _settings()
    settings.download_dir = tmp_path
    mock_uuid = MagicMock(hex="deadbeef")
    session = MagicMock()
    handle = MagicMock()

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch("app.worker.torrent_downloader.wait_for_metadata"), \
         patch("app.worker.torrent_downloader.list_files", return_value=[]), \
         patch(
             "app.worker.torrent_downloader._enforce_torrent_limits",
             side_effect=TorrentTooLargeError("too big"),
         ), \
         patch("app.worker.torrent_downloader.remove_and_delete") as mock_cleanup:
        with pytest.raises(TorrentTooLargeError):
            download_torrent("magnet:?xt=urn:btih:abc", settings)

    mock_cleanup.assert_called_once_with(session, handle, tmp_path / "torrents" / "deadbeef")


def test_download_torrent_cleans_up_on_metadata_timeout_before_handle_used(tmp_path):
    """wait_for_metadata failing must still clean up the handle/work_dir that
    add_magnet already produced, not just the ones from a later stage."""
    settings = _settings()
    settings.download_dir = tmp_path
    mock_uuid = MagicMock(hex="timeout1")
    session = MagicMock()
    handle = MagicMock()

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch(
             "app.worker.torrent_downloader.wait_for_metadata",
             side_effect=TorrentError("Timed out waiting for torrent metadata - no peers responded"),
         ), \
         patch("app.worker.torrent_downloader.remove_and_delete") as mock_cleanup:
        with pytest.raises(TorrentError, match="Timed out"):
            download_torrent("magnet:?xt=urn:btih:abc", settings)

    mock_cleanup.assert_called_once_with(session, handle, tmp_path / "torrents" / "timeout1")


def test_download_torrent_raises_when_files_missing_after_finish(tmp_path):
    settings = _settings(torrent_max_duration_seconds=60)
    settings.download_dir = tmp_path
    mock_uuid = MagicMock(hex="cafebabe")
    session = MagicMock()
    handle = MagicMock()
    files = [TorrentFile(Path("missing.mkv"), 100)]
    handle.status.return_value = MagicMock(info_hash="x", name="n", is_finished=True)

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch("app.worker.torrent_downloader.wait_for_metadata"), \
         patch("app.worker.torrent_downloader.list_files", return_value=files), \
         patch("app.worker.torrent_downloader._enforce_torrent_limits"), \
         patch("app.worker.torrent_downloader.remove_and_delete") as mock_cleanup:
        with pytest.raises(TorrentError, match="missing"):
            download_torrent("magnet:?xt=urn:btih:abc", settings)

    mock_cleanup.assert_called_once()


def test_download_torrent_raises_when_duration_limit_exceeded(tmp_path):
    settings = _settings(torrent_max_duration_seconds=10)
    settings.download_dir = tmp_path
    mock_uuid = MagicMock(hex="1234")
    session = MagicMock()
    handle = MagicMock()
    handle.status.return_value = MagicMock(info_hash="x", name="n", is_finished=False)

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch("app.worker.torrent_downloader.wait_for_metadata"), \
         patch("app.worker.torrent_downloader.list_files", return_value=[]), \
         patch("app.worker.torrent_downloader._enforce_torrent_limits"), \
         patch("app.worker.torrent_downloader.remove_and_delete") as mock_cleanup, \
         patch("app.worker.torrent_downloader.time.sleep"), \
         patch("app.worker.torrent_downloader.time.monotonic", side_effect=[0, 100]):
        with pytest.raises(TorrentError, match="time limit"):
            download_torrent("magnet:?xt=urn:btih:abc", settings)

    mock_cleanup.assert_called_once()


def test_download_torrent_polls_progress_until_finished(tmp_path):
    settings = _settings(torrent_max_duration_seconds=60)
    settings.download_dir = tmp_path
    mock_uuid = MagicMock(hex="pollpoll")
    session = MagicMock()
    handle = MagicMock()
    # No on_metadata hook is passed, so handle.status() is only called from
    # the polling loop itself - one call per iteration.
    handle.status.side_effect = [
        MagicMock(is_finished=False),  # loop iter 1
        MagicMock(is_finished=False),  # loop iter 2
        MagicMock(is_finished=True),   # loop iter 3
    ]
    progress_calls = []

    with patch("app.worker.torrent_downloader.uuid.uuid4", return_value=mock_uuid), \
         patch("app.worker.torrent_downloader.build_session", return_value=session), \
         patch("app.worker.torrent_downloader.add_magnet", return_value=handle), \
         patch("app.worker.torrent_downloader.wait_for_metadata"), \
         patch("app.worker.torrent_downloader.list_files", return_value=[]), \
         patch("app.worker.torrent_downloader._enforce_torrent_limits"), \
         patch("app.worker.torrent_downloader.time.sleep") as mock_sleep:
        download_torrent(
            "magnet:?xt=urn:btih:abc", settings,
            on_progress=lambda p: progress_calls.append(p),
        )

    assert len(progress_calls) == 3
    assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# remove_and_delete
# ---------------------------------------------------------------------------

def test_remove_and_delete_removes_handle_and_wipes_work_dir(tmp_path):
    session = MagicMock()
    handle = MagicMock()
    work_dir = tmp_path / "abc"
    work_dir.mkdir(parents=True)
    (work_dir / "f.txt").write_bytes(b"x")

    remove_and_delete(session, handle, work_dir)

    session.remove_torrent.assert_called_once_with(handle, lt.session.delete_files)
    assert not work_dir.exists()


def test_remove_and_delete_skips_removal_when_handle_is_none(tmp_path):
    session = MagicMock()
    work_dir = tmp_path / "abc"
    work_dir.mkdir(parents=True)

    remove_and_delete(session, None, work_dir)

    session.remove_torrent.assert_not_called()
    assert not work_dir.exists()


def test_remove_and_delete_swallows_remove_torrent_errors(tmp_path, caplog):
    session = MagicMock()
    session.remove_torrent.side_effect = RuntimeError("boom")
    handle = MagicMock()
    work_dir = tmp_path / "abc"
    work_dir.mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="app.worker.torrent_downloader"):
        remove_and_delete(session, handle, work_dir)

    assert not work_dir.exists()
    assert "Failed to remove torrent handle" in caplog.text


def test_remove_and_delete_ignores_missing_work_dir(tmp_path):
    session = MagicMock()
    work_dir = tmp_path / "never-created"

    remove_and_delete(session, None, work_dir)  # no exception
