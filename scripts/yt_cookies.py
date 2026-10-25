#!/usr/bin/env python3
"""Обновление cookies YouTube из локального профиля Chrome в БД бота.

Режимы:
  refresh  (по умолчанию) открывает youtube.com в headless Chrome с постоянным профилем,
           чтобы браузер сам обновил (ротировал) cookies, затем сохраняет в БД только
           домены YouTube/Google. Если аккаунт не авторизован — БД не трогает.
  login    запускает Chrome с удалённой отладкой (127.0.0.1:9222) для одноразового входа
           в отдельный аккаунт Google через SSH-туннель.
  filter   оставляет в БД только cookies YouTube/Google (без обращения к браузеру).

Настройки берутся из переменных окружения (см. /etc/yt-cookies.env):
  YT_DATABASE_URL, YT_USER_ID, YT_PROFILE_DIR, YT_CHROME
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg2

DATABASE_URL = os.environ.get("YT_DATABASE_URL", "")
USER_ID = int(os.environ.get("YT_USER_ID", "0"))
PROFILE_DIR = os.environ.get("YT_PROFILE_DIR", "/srv/chrome-youtube")
CHROME = os.environ.get("YT_CHROME", "/usr/bin/google-chrome")
PLATFORM = "youtube"
LOGIN_DONE_FILE = "/tmp/yt-login.done"
BOT_ENV_FILE = os.environ.get("YT_BOT_ENV_FILE", "/srv/projects/Video_down/.env")
STATE_FILE = Path(os.environ.get("YT_STATE_FILE", "/var/lib/yt-cookies/state.json"))
RUN_DIR = Path(os.environ.get("YT_RUN_DIR", "/srv/projects/Video_down/run"))
REPEAT_ALERT_SECONDS = 24 * 3600  # о той же проблеме напоминаем не чаще раза в сутки

# Нужны yt-dlp для YouTube; cookies остальных сервисов Google (mail, drive, ads...) не храним.
_YOUTUBE_SUFFIXES = ("youtube.com", "youtube-nocookie.com", "googlevideo.com", "ytimg.com")
_GOOGLE_EXACT = {"google.com", "www.google.com", "accounts.google.com"}
# Признак авторизованной сессии.
_AUTH_COOKIES = {"SID", "__Secure-3PSID", "__Secure-1PSID", "SAPISID"}


def _domain_ok(domain: str) -> bool:
    d = domain.lstrip(".").lower()
    if any(d == x or d.endswith("." + x) for x in _YOUTUBE_SUFFIXES):
        return True
    if d in _GOOGLE_EXACT:
        return True
    # корневые домены Google в других зонах (google.pl, google.com.ua): там лежат SID/SAPISID
    return d.startswith("google.")


def to_netscape(cookies: list[dict]) -> tuple[str, int]:
    lines = ["# Netscape HTTP Cookie File"]
    kept = 0
    for c in cookies:
        if not _domain_ok(c["domain"]):
            continue
        expires = c.get("expires", -1)
        expiry = int(expires) if expires and expires > 0 else 0
        domain = c["domain"]
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append("\t".join([
            prefix + domain, include_sub, c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE", str(expiry), c["name"], c["value"],
        ]))
        kept += 1
    return "\n".join(lines) + "\n", kept


def parse_netscape(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        p = line.split("\t")
        if len(p) < 7:
            continue
        out.append({
            "domain": p[0], "path": p[2], "secure": p[3] == "TRUE",
            "expires": int(p[4]) if p[4].isdigit() else 0,
            "name": p[5], "value": p[6], "httpOnly": http_only,
        })
    return out


def _connect():
    if not DATABASE_URL or not USER_ID:
        sys.exit("Не заданы YT_DATABASE_URL / YT_USER_ID")
    return psycopg2.connect(DATABASE_URL)


def save(text: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_cookies_video_bot (user_id, platform, cookies_text, created_at, updated_at)
            VALUES (%s, %s, %s, now(), now())
            ON CONFLICT (user_id, platform)
            DO UPDATE SET cookies_text = EXCLUDED.cookies_text, updated_at = now()
            """,
            (USER_ID, PLATFORM, text),
        )


def _read_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as exc:
        print(f"Не удалось прочитать {path}: {exc}", file=sys.stderr)
    return out


def notify_admins(text: str) -> bool:
    """Шлёт сообщение всем ADMIN_USERS из конфигурации бота. Возвращает True, если хоть одно дошло."""
    env = _read_env(BOT_ENV_FILE)
    token = env.get("BOT_TOKEN", "")
    admins = [a.strip() for a in env.get("ADMIN_USERS", "").split(",") if a.strip().lstrip("-").isdigit()]
    if not token or not admins:
        print("Telegram: нет BOT_TOKEN или ADMIN_USERS", file=sys.stderr)
        return False
    sent = False
    for chat_id in admins:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=20
            ) as resp:
                sent = json.load(resp).get("ok", False) or sent
        except Exception as exc:  # токен в тексте ошибки не печатаем
            print(f"Telegram: не удалось отправить {chat_id}: {type(exc).__name__}", file=sys.stderr)
    return sent


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except OSError as exc:
        print(f"Не удалось сохранить состояние: {exc}", file=sys.stderr)


def report_result(ok: bool, problem: str = "") -> None:
    """Уведомляет администраторов о сбое (не чаще раза в сутки) и о восстановлении."""
    state = _load_state()
    now = time.time()
    if ok:
        if state.get("failing"):
            notify_admins("✅ Cookies YouTube снова обновляются, проблема устранена.")
            _save_state({"failing": False})
        return
    if state.get("failing") and now - state.get("last_alert", 0) < REPEAT_ALERT_SECONDS:
        return
    if notify_admins(f"⚠️ Не удалось обновить cookies YouTube: {problem}\nЗагрузки с YouTube могут перестать работать."):
        _save_state({"failing": True, "last_alert": now})
    else:
        _save_state({"failing": True, "last_alert": state.get("last_alert", 0)})


def cmd_filter() -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cookies_text FROM user_cookies_video_bot WHERE user_id=%s AND platform=%s",
            (USER_ID, PLATFORM),
        )
        row = cur.fetchone()
    if not row:
        print("cookies в БД нет")
        return 1
    before = parse_netscape(row[0])
    text, kept = to_netscape(before)
    save(text)
    print(f"было {len(before)}, осталось {kept}")
    return 0


def _refresh() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR, executable_path=CHROME, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)  # даём YouTube выдать обновлённые *PSIDTS/*PSIDCC
            cookies = ctx.cookies()
        finally:
            ctx.close()
    names = {c["name"] for c in cookies if _domain_ok(c["domain"])}
    if not names & _AUTH_COOKIES:
        print("Аккаунт не авторизован в профиле — БД не изменена (запустите login)", file=sys.stderr)
        report_result(False, "аккаунт Google в профиле Chrome больше не авторизован, нужен повторный вход (yt_cookies.py login).")
        return 2
    text, kept = to_netscape(cookies)
    save(text)
    print(f"cookies обновлены: {kept} шт.")
    report_result(True)
    return 0


def _answer_worker(ok: bool) -> None:
    """Если воркер ждёт обновления (файл-запрос в RUN_DIR), снимаем запрос и сообщаем результат."""
    request = RUN_DIR / "refresh-cookies.request"
    try:
        request.unlink(missing_ok=True)
        if RUN_DIR.is_dir():
            (RUN_DIR / "refresh-cookies.result").write_text("ok" if ok else "fail")
    except OSError as exc:
        print(f"Не удалось ответить воркеру: {exc}", file=sys.stderr)


def cmd_refresh() -> int:
    ok = False
    try:
        code = _refresh()
        ok = code == 0
        return code
    except Exception as exc:
        report_result(False, f"ошибка запуска Chrome или записи в БД ({type(exc).__name__}: {str(exc)[:150]}).")
        raise
    finally:
        _answer_worker(ok)


def cmd_login() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR, executable_path=CHROME, headless=True,
            args=["--no-sandbox", "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
                  "--remote-allow-origins=*", "--window-size=1280,900"],
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://accounts.google.com/ServiceLogin?service=youtube", timeout=60000)
        print("Chrome запущен. Откройте на своём компьютере: ssh -L 9222:127.0.0.1:9222 <сервер>,")
        print("затем в Chrome: http://localhost:9222 → страница → вход в аккаунт.")
        print(f"Когда войдёте, создайте файл {LOGIN_DONE_FILE} (touch) — Chrome закроется и профиль сохранится.", flush=True)
        os.path.exists(LOGIN_DONE_FILE) and os.remove(LOGIN_DONE_FILE)
        deadline = time.time() + 30 * 60  # без сигнала закрываемся через 30 минут
        while time.time() < deadline and not os.path.exists(LOGIN_DONE_FILE):
            page.wait_for_timeout(2000)
        os.path.exists(LOGIN_DONE_FILE) and os.remove(LOGIN_DONE_FILE)
        ctx.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="refresh", choices=["refresh", "login", "filter"])
    args = ap.parse_args()
    return {"refresh": cmd_refresh, "login": cmd_login, "filter": cmd_filter}[args.mode]()


if __name__ == "__main__":
    sys.exit(main())
