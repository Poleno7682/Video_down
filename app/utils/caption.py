from __future__ import annotations

from app.core.config import Settings

# Telegram ограничивает длину подписи 1024 символами.
_MAX_CAPTION = 1024
# Запасной текст, если файл подписи отсутствует или пуст.
DEFAULT_CAPTION = "Спасибо за использование @fbtt_download_bot"


def get_caption(settings: Settings, title: str | None = None) -> str:
    """Постоянная подпись под каждым отправленным видео.

    Текст читается из ``settings.caption_file`` при каждом вызове, поэтому
    его можно менять мгновенно — достаточно отредактировать файл (он
    примонтирован как volume), пересборка и перезапуск не требуются.

    title, when given (currently only for rezka.ag videos — see callers),
    is prepended as its own line, e.g. "Форрест Гамп, Дубляж".
    """
    try:
        text = settings.caption_file.read_text(encoding="utf-8").strip()
        if not text:
            text = DEFAULT_CAPTION
    except OSError:
        text = DEFAULT_CAPTION

    if title:
        text = f"{title}\n{text}"
    return text[:_MAX_CAPTION]
