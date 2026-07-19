from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.services.cleanup import cleanup_stale_downloads


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove stale entries from downloads/active/.")
    parser.add_argument(
        "max_age_hours",
        type=float,
        nargs="?",
        default=None,
        help="Remove entries older than this many hours (default: STALE_FILE_MAX_AGE_HOURS from .env). "
        "Pass 0 to remove everything regardless of age.",
    )
    args = parser.parse_args()

    settings = get_settings()
    removed = cleanup_stale_downloads(settings, args.max_age_hours)
    print(f"Removed {removed} stale download entries.")


if __name__ == "__main__":
    main()
