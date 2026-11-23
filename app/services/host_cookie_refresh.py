"""Запрос на внешнее обновление cookies YouTube/TikTok (Chrome на хосте).

Воркер работает в контейнере и не может сам запустить браузер, поэтому обмен идёт через
общую папку RUN_DIR: воркер создаёт файл-запрос, systemd на хосте (yt-cookies-refresh.path)
запускает scripts/yt_cookies.py refresh, который обновляет cookies в БД, удаляет запрос и
записывает результат ("ok" или "fail"). Воркер ждёт результат и повторяет загрузку.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_DIR = Path(os.environ.get("HOST_REFRESH_DIR", "/app/run"))
# YouTube — исторические имена файлов; для остальных платформ добавляется суффикс "-<платформа>".
_WAIT_SECONDS = 120
_POLL_SECONDS = 2
# Если cookies только что обновляли и ошибка осталась — повторное обновление не поможет.
_COOLDOWN_SECONDS = 10 * 60
COOLDOWN_KEY = "yt_host_cookie_refresh:cooldown"  # YouTube; для прочих — с суффиксом ":<платформа>"


def _names(platform: str) -> tuple[Path, Path, str]:
    if platform == "youtube":
        return RUN_DIR / "refresh-cookies.request", RUN_DIR / "refresh-cookies.result", COOLDOWN_KEY
    return (
        RUN_DIR / f"refresh-cookies-{platform}.request",
        RUN_DIR / f"refresh-cookies-{platform}.result",
        f"{COOLDOWN_KEY}:{platform}",
    )


def request_host_refresh(redis=None, platform: str = "youtube") -> bool:
    """Просит хост обновить cookies и ждёт результат. True — cookies обновлены и можно повторять загрузку."""
    request_file, result_file, cooldown_key = _names(platform)
    if not RUN_DIR.is_dir():
        logger.info("Папка обмена %s недоступна — внешнее обновление cookies не настроено", RUN_DIR)
        return False
    if redis is not None:
        try:
            if redis.exists(cooldown_key):
                logger.info("Cookies обновлялись менее %s мин. назад — повторно не обновляем", _COOLDOWN_SECONDS // 60)
                return False
        except Exception:
            logger.exception("Не удалось проверить паузу между обновлениями cookies")

    started = time.time()
    try:
        request_file.write_text(str(int(started)))
    except OSError:
        logger.exception("Не удалось создать запрос на обновление cookies")
        return False

    deadline = started + _WAIT_SECONDS
    while time.time() < deadline:
        try:
            if result_file.exists() and result_file.stat().st_mtime >= started - 1:
                ok = result_file.read_text().strip() == "ok"
                logger.info("Результат внешнего обновления cookies: %s", "ok" if ok else "fail")
                if ok and redis is not None:
                    try:
                        redis.setex(cooldown_key, _COOLDOWN_SECONDS, "1")
                    except Exception:
                        logger.exception("Не удалось выставить паузу между обновлениями cookies")
                return ok
        except OSError:
            pass
        time.sleep(_POLL_SECONDS)
    logger.warning("Внешнее обновление cookies не завершилось за %s с", _WAIT_SECONDS)
    return False
