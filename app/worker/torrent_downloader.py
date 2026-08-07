from __future__ import annotations

import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import libtorrent as lt

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Well-known public bootstrap nodes so DHT can find peers even for a
# trackerless magnet (xt=urn:btih:<hash> with no tracker list at all) or one
# whose own trackers are down.
_DHT_BOOTSTRAP_NODES = (
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
)

_METADATA_POLL_INTERVAL_SECONDS = 1
_STATUS_POLL_INTERVAL_SECONDS = 2


class TorrentError(RuntimeError):
    """Unrecoverable magnet/torrent-download failure."""


class TorrentTooLargeError(TorrentError):
    """Total torrent content exceeds settings.torrent_max_total_size_mb."""


class TorrentTooManyFilesError(TorrentError):
    """Torrent has more files than settings.torrent_max_file_count."""


@dataclass(frozen=True)
class TorrentFile:
    path: Path
    size_bytes: int


@dataclass(frozen=True)
class TorrentProgress:
    """Snapshot of a torrent_handle.status() reduced to what the Telegram
    status message shows: speed, and peers/seeds both connected right now
    and available in the swarm overall (from tracker/DHT scrape - -1 when
    the swarm hasn't reported a number yet).
    """
    state: str
    progress: float
    download_rate: float
    upload_rate: float
    peers_connected: int
    seeds_connected: int
    peers_available: int
    seeds_available: int
    total_size: int
    downloaded: int


def build_session(settings: Settings) -> "lt.session":
    """A libtorrent session tuned for leech-only use.

    DHT/LSD stay on - needed to resolve trackerless magnets and to find
    peers a magnet's own tracker list misses. UPnP/NATPMP stay off - no
    reason for a download-only client to ask the router to open ports.
    Upload is throttled to a low but nonzero rate (see
    Settings.torrent_upload_limit_kbps) rather than fully disabled - some
    upload is needed to satisfy peers' tit-for-tat unchoking, and the
    torrent is removed the instant the download finishes anyway (see
    remove_and_delete), so there is never an actual seeding phase.
    """
    session = lt.session()
    session.apply_settings({
        "listen_interfaces": settings.torrent_listen_interfaces,
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": False,
        "enable_natpmp": False,
        "download_rate_limit": 0,
        "upload_rate_limit": max(1, settings.torrent_upload_limit_kbps) * 1024,
    })
    for host, port in _DHT_BOOTSTRAP_NODES:
        session.add_dht_router(host, port)
    return session


def add_magnet(session: "lt.session", magnet_uri: str, save_path: Path) -> "lt.torrent_handle":
    params = lt.parse_magnet_uri(magnet_uri)
    params.save_path = str(save_path)
    return session.add_torrent(params)


def wait_for_metadata(handle: "lt.torrent_handle", timeout_seconds: int) -> None:
    """Block until the swarm/DHT hands back the torrent's file list.

    A magnet URI carries only an info-hash - the actual metadata (file
    names/sizes) has to come from a peer, so nothing about total size or
    file count is knowable before this returns.
    """
    deadline = time.monotonic() + timeout_seconds
    while not handle.status().has_metadata:
        if time.monotonic() > deadline:
            raise TorrentError("Timed out waiting for torrent metadata - no peers responded")
        time.sleep(_METADATA_POLL_INTERVAL_SECONDS)


def list_files(handle: "lt.torrent_handle") -> list[TorrentFile]:
    storage = handle.torrent_file().files()
    return [
        TorrentFile(path=Path(storage.file_path(i)), size_bytes=storage.file_size(i))
        for i in range(storage.num_files())
    ]


def status_to_progress(status) -> TorrentProgress:
    return TorrentProgress(
        state=str(status.state),
        progress=status.progress,
        download_rate=status.download_rate,
        upload_rate=status.upload_rate,
        peers_connected=status.num_peers,
        seeds_connected=status.num_seeds,
        peers_available=status.num_incomplete,
        seeds_available=status.num_complete,
        total_size=status.total_wanted,
        downloaded=status.total_wanted_done,
    )


def _enforce_torrent_limits(files: list[TorrentFile], settings: Settings) -> None:
    total_size = sum(f.size_bytes for f in files)
    max_bytes = settings.torrent_max_total_size_mb * 1024 * 1024
    if settings.torrent_max_total_size_mb > 0 and total_size > max_bytes:
        raise TorrentTooLargeError(
            f"Torrent content is {total_size / (1024 * 1024):.1f} MB, "
            f"exceeds the {settings.torrent_max_total_size_mb} MB limit"
        )
    if settings.torrent_max_file_count > 0 and len(files) > settings.torrent_max_file_count:
        raise TorrentTooManyFilesError(
            f"Torrent has {len(files)} files, exceeds the {settings.torrent_max_file_count} file limit"
        )


def download_torrent(
    magnet_uri: str,
    settings: Settings,
    *,
    on_metadata: Callable[[str, str, list[TorrentFile]], None] | None = None,
    on_progress: Callable[[TorrentProgress], None] | None = None,
) -> tuple["lt.session", "lt.torrent_handle", Path, list[Path]]:
    """Download every file of a magnet link into its own temp directory.

    On success, returns (session, handle, work_dir, file_paths); the caller
    owns cleanup via remove_and_delete() once it's done sending those files.
    On any failure this cleans up after itself (removes the torrent from the
    session and deletes work_dir) before re-raising, since a caller that
    never got a successful return also never got a handle to clean up with.

    on_metadata fires once, right after the file list becomes known - used
    to persist info_hash/name and to reject oversized/too-many-file
    torrents before a single piece is downloaded. on_progress fires
    repeatedly while the download runs, throttling is the caller's job (the
    poll loop itself only runs once every _STATUS_POLL_INTERVAL_SECONDS).
    """
    work_dir = settings.download_dir / "torrents" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    session = build_session(settings)
    handle: "lt.torrent_handle | None" = None
    try:
        handle = add_magnet(session, magnet_uri, work_dir)
        wait_for_metadata(handle, settings.torrent_metadata_timeout_seconds)

        files = list_files(handle)
        _enforce_torrent_limits(files, settings)

        if on_metadata:
            status = handle.status()
            on_metadata(str(status.info_hash), status.name, files)

        deadline = time.monotonic() + settings.torrent_max_duration_seconds
        while True:
            status = handle.status()
            if on_progress:
                on_progress(status_to_progress(status))
            if status.is_finished:
                break
            if time.monotonic() > deadline:
                raise TorrentError("Torrent download exceeded the configured time limit")
            time.sleep(_STATUS_POLL_INTERVAL_SECONDS)

        file_paths = [work_dir / f.path for f in files]
        missing = [p for p in file_paths if not p.exists()]
        if missing:
            raise TorrentError(f"{len(missing)} file(s) missing after torrent reported finished")

        return session, handle, work_dir, file_paths
    except Exception:
        remove_and_delete(session, handle, work_dir)
        raise


def remove_and_delete(
    session: "lt.session", handle: "lt.torrent_handle | None", work_dir: Path
) -> None:
    """Stop seeding immediately and wipe every downloaded byte.

    Called right after the downloaded files have been sent to Telegram, or
    on any failure - nothing from a magnet download is ever kept around or
    reshared once the bot is done with it.
    """
    if handle is not None:
        try:
            session.remove_torrent(handle, lt.session.delete_files)
        except Exception:
            logger.warning("Failed to remove torrent handle cleanly", exc_info=True)
    shutil.rmtree(work_dir, ignore_errors=True)
