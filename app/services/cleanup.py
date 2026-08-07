from __future__ import annotations

import logging
import shutil
import time

from app.core.config import Settings

logger = logging.getLogger(__name__)


def cleanup_stale_downloads(settings: Settings, max_age_hours: float | None = None) -> int:
    """Delete entries under download_dir/active and download_dir/torrents
    older than max_age_hours.

    Safety net, not the primary cleanup mechanism — per-request cleanup
    already runs in app.worker.tasks (deleting a sent file right after
    upload, or on failure after download; a torrent's whole work dir via
    app.worker.torrent_downloader.remove_and_delete). This exists for
    whatever still slips through: a worker killed mid-task (OOM,
    force-restart), a code path that doesn't clean up on failure, or simply
    old cached files the operator wants gone. Pass max_age_hours=0 to remove
    everything regardless of age. Returns the number of entries removed.
    """
    if max_age_hours is None:
        max_age_hours = settings.stale_file_max_age_hours
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for subdir in ("active", "torrents"):
        target_dir = settings.download_dir / subdir
        if not target_dir.exists():
            continue
        for entry in target_dir.iterdir():
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError:
                logger.warning("Failed to remove stale download entry %s", entry)
                continue
            removed += 1

    if removed:
        logger.info("Cleanup removed %d stale download entries older than %sh", removed, max_age_hours)
    return removed
