from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    # "webhook" — Telegram шлёт апдейты на наш HTTPS-эндпоинт (нужен белый IP/домен/SSL).
    # "polling" — бот сам опрашивает Telegram исходящими запросами (работает за NAT).
    bot_mode: str = Field(default="webhook", alias="BOT_MODE")
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    webhook_path: str = Field(default="/telegram/webhook", alias="WEBHOOK_PATH")
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    celery_broker_url: str = Field(alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(alias="CELERY_RESULT_BACKEND")

    download_dir: Path = Field(default=Path("/app/downloads"), alias="DOWNLOAD_DIR")
    cookie_dir: Path = Field(default=Path("/app/cookies"), alias="COOKIE_DIR")
    log_dir: Path = Field(default=Path("/app/logs"), alias="LOG_DIR")
    # Подпись под каждым отправленным видео. Читается из файла при каждой
    # отправке, чтобы текст можно было менять без пересборки/перезапуска.
    caption_file: Path = Field(default=Path("/app/caption.txt"), alias="CAPTION_FILE")

    allowed_users: str = Field(default="", alias="ALLOWED_USERS")
    admin_users: str = Field(default="", alias="ADMIN_USERS")

    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")
    rate_limit_max_messages: int = Field(default=8, alias="RATE_LIMIT_MAX_MESSAGES")
    ban_seconds: int = Field(default=600, alias="BAN_SECONDS")
    user_daily_limit: int = Field(default=50, alias="USER_DAILY_LIMIT")
    user_queue_limit: int = Field(default=3, alias="USER_QUEUE_LIMIT")
    global_queue_limit: int = Field(default=50, alias="GLOBAL_QUEUE_LIMIT")

    default_quality: str = Field(default="720p", alias="DEFAULT_QUALITY")
    # Standard Telegram Bot API limit is 50 MB. Set MAX_FILE_MB=2000 only when
    # USE_LOCAL_BOT_API=true (see below) — the standard cloud API rejects
    # anything over 50 MB regardless of this setting.
    max_file_mb: int = Field(default=50, alias="MAX_FILE_MB")
    # Route Bot API calls through a self-hosted Local Bot API server
    # (https://core.telegram.org/bots/api#using-a-local-bot-api-server)
    # instead of api.telegram.org, which raises the upload/download limit
    # from 50 MB to 2000 MB. Requires TELEGRAM_API_ID/TELEGRAM_API_HASH on
    # the telegram-bot-api service in docker-compose.yml (from
    # https://my.telegram.org/apps).
    use_local_bot_api: bool = Field(default=False, alias="USE_LOCAL_BOT_API")
    local_bot_api_url: str = Field(default="http://telegram-bot-api:8081", alias="LOCAL_BOT_API_URL")
    # Routes all yt-dlp requests (download + livestream pre-check) through a
    # proxy, e.g. socks5h://user:pass@host:1080 — most commonly to dodge
    # anti-bot blocks tied to a VPS's datacenter IP range (YouTube's "Sign in
    # to confirm you're not a bot"). Empty = no proxy, yt-dlp connects
    # directly. socks5h (vs. socks5) resolves DNS through the proxy too.
    ytdlp_proxy: str = Field(default="", alias="YTDLP_PROXY")
    # Dedicated last-resort proxy for rezka only, routed through the local
    # `vpn` docker-compose service (an OpenVPN client container) rather than
    # the shared free/cheap YTDLP_PROXY pool — used only once direct and
    # every pool proxy have already failed for a rezka request. Empty =
    # feature off, matching every other proxy setting here.
    rezka_vpn_proxy_url: str = Field(default="", alias="REZKA_VPN_PROXY_URL")
    # rezka.ag/hdrezka.* sit behind an Anubis proof-of-work JS challenge a
    # plain HTTP request can never pass — solving it requires actually
    # running the client-side JS, so this drives a real headless Chromium
    # (via Playwright) through the challenge before every rezka download
    # when enabled. Off by default: launches a full browser process per
    # rezka request, meaningfully heavier than every other download path.
    # Requires the Playwright Chromium browser installed in the image (see
    # Dockerfile's `playwright install chromium` step).
    rezka_antibot_bypass: bool = Field(default=False, alias="REZKA_ANTIBOT_BYPASS")
    download_timeout_seconds: int = Field(default=900, alias="DOWNLOAD_TIMEOUT_SECONDS")
    max_active_downloads_per_user: int = Field(default=1, alias="MAX_ACTIVE_DOWNLOADS_PER_USER")
    max_download_duration_seconds: int = Field(default=1800, alias="MAX_DOWNLOAD_DURATION_SECONDS")

    cache_ttl_hours: int = Field(default=168, alias="CACHE_TTL_HOURS")
    delete_local_file_after_telegram_cache: bool = Field(default=True, alias="DELETE_LOCAL_FILE_AFTER_TELEGRAM_CACHE")
    # Safety-net sweep of downloads/active/ for anything the per-request
    # cleanup missed (a worker OOM-killed mid-task, a code path that doesn't
    # clean up on failure, etc.) — see app/services/cleanup.py.
    stale_file_max_age_hours: float = Field(default=24, alias="STALE_FILE_MAX_AGE_HOURS")

    # Admin broadcast mode auto-expires after this many seconds of inactivity.
    # Each broadcast message resets the timer (implemented via Redis key TTL).
    broadcast_timeout_seconds: int = Field(default=300, alias="BROADCAST_TIMEOUT_SECONDS")

    # Hard-burn subtitles (native or auto-generated, ru/en) into the video
    # picture when yt-dlp finds them. Off by default: adds an extra ffmpeg
    # re-encode pass to every download.
    embed_subtitles: bool = Field(default=False, alias="EMBED_SUBTITLES")

    use_cookies: bool = Field(default=True, alias="USE_COOKIES")
    facebook_cookies_file: Path = Field(default=Path("/app/cookies/facebook.txt"), alias="FACEBOOK_COOKIES_FILE")
    instagram_cookies_file: Path = Field(default=Path("/app/cookies/instagram.txt"), alias="INSTAGRAM_COOKIES_FILE")
    tiktok_cookies_file: Path = Field(default=Path("/app/cookies/tiktok.txt"), alias="TIKTOK_COOKIES_FILE")
    youtube_cookies_file: Path = Field(default=Path("/app/cookies/youtube.txt"), alias="YOUTUBE_COOKIES_FILE")

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8080, alias="APP_PORT")

    _allowed_user_ids: set[int] = PrivateAttr()
    _admin_user_ids: set[int] = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        self._allowed_user_ids = _parse_ids(self.allowed_users)
        self._admin_user_ids = _parse_ids(self.admin_users)

    @property
    def webhook_url(self) -> str:
        return self.webhook_base_url.rstrip("/") + self.webhook_path

    @property
    def allowed_user_ids(self) -> set[int]:
        return self._allowed_user_ids

    @property
    def admin_user_ids(self) -> set[int]:
        return self._admin_user_ids


def _parse_ids(raw: str) -> set[int]:
    result = set()
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        if x.isdigit():
            result.add(int(x))
        else:
            logger.warning("Invalid user ID in config, skipping: %r", x)
    return result


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.cookie_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings
