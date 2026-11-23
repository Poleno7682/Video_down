"""TikTok photo posts (/photo/<id>): yt-dlp не умеет их скачивать.

Страница поста (при загрузке через curl_cffi с impersonation) содержит
`imagePost.images` и `music.playUrl`. Мы забираем картинки и звук и собираем
из них mp4-слайдшоу, которое дальше идёт по обычному пайплайну.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# URL фото-поста берём прямо из текста ошибки yt-dlp «Unsupported URL: …»:
# короткие ссылки vm.tiktok.com он уже сам развернул в итоговый адрес.
_PHOTO_URL_RE = re.compile(r"https?://[^\s'\"]*tiktok\.com/@[^/\s]+/photo/(\d+)[^\s'\"]*")
_REHYDRATION_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S
)

_HTTP_TIMEOUT = 30
# Сколько секунд показывается каждое фото (музыка зацикливается/обрезается под итоговую длину).
SECONDS_PER_IMAGE = 5.0
_MAX_IMAGES = 60
_FFMPEG_TIMEOUT = 600
_FRAME_W, _FRAME_H = 1080, 1920


class TikTokPhotoError(RuntimeError):
    pass


def find_photo_url(text: str) -> str | None:
    """Вернуть URL TikTok фото-поста из строки (или None)."""
    m = _PHOTO_URL_RE.search(text or "")
    return m.group(0) if m else None


def parse_photo_post(html: str) -> tuple[list[str], str | None, str]:
    """(urls картинок, url музыки или None, заголовок) из HTML страницы поста."""
    m = _REHYDRATION_RE.search(html)
    if not m:
        raise TikTokPhotoError("TikTok page has no embedded data")
    try:
        scope = json.loads(m.group(1))["__DEFAULT_SCOPE__"]
        item = scope["webapp.video-detail"]["itemInfo"]["itemStruct"]
    except (KeyError, ValueError) as exc:
        raise TikTokPhotoError("TikTok post data not found in page") from exc
    images: list[str] = []
    for img in (item.get("imagePost") or {}).get("images") or []:
        urls = (img.get("imageURL") or {}).get("urlList") or []
        if urls:
            images.append(urls[0])
    if not images:
        raise TikTokPhotoError("TikTok post has no images")
    music = (item.get("music") or {}).get("playUrl") or None
    return images[:_MAX_IMAGES], music, item.get("desc") or ""


def build_slideshow_command(
    images: list[Path], audio: Path | None, per_image: float, output: Path
) -> list[str]:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for img in images:
        cmd += ["-loop", "1", "-t", f"{per_image:.2f}", "-i", str(img)]
    n = len(images)
    if audio:
        cmd += ["-stream_loop", "-1", "-i", str(audio)]
    scale = (
        f"scale={_FRAME_W}:{_FRAME_H}:force_original_aspect_ratio=decrease,"
        f"pad={_FRAME_W}:{_FRAME_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,format=yuv420p"
    )
    parts = [f"[{i}:v]{scale}[v{i}]" for i in range(n)]
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if audio:
        cmd += ["-map", f"{n}:a", "-c:a", "aac", "-b:a", "128k"]
    cmd += [
        "-t", f"{per_image * n:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-movflags", "+faststart", str(output),
    ]
    return cmd


def download_photo_post(
    url: str,
    work_dir: Path,
    *,
    proxy: str | None = None,
) -> tuple[Path, dict]:
    """Скачать фото-пост и собрать mp4. Возвращает (путь к mp4, info)."""
    from curl_cffi import requests as cffi_requests  # ленивый импорт: тяжёлая зависимость

    post_id = _PHOTO_URL_RE.search(url).group(1)  # type: ignore[union-attr]
    # Страница /photo/ без impersonation и с /photo/ в пути отдаёт пустую заготовку —
    # данные приходят только по /video/-варианту того же поста.
    page_url = re.sub(r"/photo/", "/video/", url.split("?")[0])
    proxies = {"http": proxy, "https": proxy} if proxy else None
    session = cffi_requests.Session(impersonate="chrome", proxies=proxies)

    resp = session.get(page_url, timeout=_HTTP_TIMEOUT)
    if resp.status_code != 200:
        raise TikTokPhotoError(f"TikTok page returned HTTP {resp.status_code}")
    image_urls, music_url, title = parse_photo_post(resp.text)

    images: list[Path] = []
    for i, img_url in enumerate(image_urls):
        r = session.get(img_url, timeout=_HTTP_TIMEOUT, headers={"Referer": "https://www.tiktok.com/"})
        if r.status_code != 200 or not r.content:
            raise TikTokPhotoError(f"image {i} download failed: HTTP {r.status_code}")
        path = work_dir / f"img_{i:03d}.img"
        path.write_bytes(r.content)
        images.append(path)

    audio: Path | None = None
    if music_url:
        r = session.get(music_url, timeout=_HTTP_TIMEOUT, headers={"Referer": "https://www.tiktok.com/"})
        if r.status_code == 200 and r.content:
            audio = work_dir / "music.mp3"
            audio.write_bytes(r.content)
        else:
            logger.warning("TikTok photo %s: music download failed (HTTP %s)", post_id, r.status_code)

    per_image = SECONDS_PER_IMAGE
    output = work_dir / f"tiktok_photo_{post_id}.mp4"
    proc = subprocess.run(
        build_slideshow_command(images, audio, per_image, output),
        capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0 or not output.exists():
        raise TikTokPhotoError(f"ffmpeg slideshow failed: {proc.stderr[-300:]}")
    return output, {"title": title, "id": post_id}
