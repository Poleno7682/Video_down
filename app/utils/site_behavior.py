from __future__ import annotations

from dataclasses import dataclass

from app.utils.rezka import is_rezka_url


@dataclass(frozen=True)
class SiteBehavior:
    """Non-generic per-site download behavior, keyed off the URL.

    Everything outside this module treats a site as plain yt-dlp (the
    SiteBehavior() default) unless site_behavior() below says otherwise —
    add a new platform's quirks as another branch there (and a dedicated
    Settings field if it needs one, like dedicated_proxy_setting) instead of
    adding another is_rezka_url()-style check at a new call site in
    app.worker.tasks / app.worker.downloader / app.bot.routers.url_handler.
    """
    has_custom_resolver: bool = False
    caption_uses_resolved_title: bool = False
    skip_livestream_check: bool = False
    needs_origin_headers: bool = False
    retry_whole_cycle_on_failure: bool = False
    dedicated_proxy_setting: str | None = None


_GENERIC = SiteBehavior()

# rezka.ag has no yt-dlp extractor: its stream URL must be resolved via
# HdRezkaApi first (has_custom_resolver), the resolved CDN checks
# Referer/Origin against the page's own site (needs_origin_headers), it's
# VOD-only so the generic livestream precheck can only ever fail on it
# (skip_livestream_check), a transient CDN failure is worth one full retry
# including a fresh antibot solve (retry_whole_cycle_on_failure), and it
# routes through its own dedicated proxy rather than the shared yt-dlp pool
# (dedicated_proxy_setting) — see app.worker.downloader.download_video and
# app.worker.tasks._resolve_proxies for where each of these is consumed.
_REZKA = SiteBehavior(
    has_custom_resolver=True,
    caption_uses_resolved_title=True,
    skip_livestream_check=True,
    needs_origin_headers=True,
    retry_whole_cycle_on_failure=True,
    dedicated_proxy_setting="rezka_vpn_proxy_url",
)


def site_behavior(url: str) -> SiteBehavior:
    return _REZKA if is_rezka_url(url) else _GENERIC
