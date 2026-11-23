#!/usr/bin/env python3
"""Обновление cookies YouTube из локального профиля Chrome в БД бота.

Режимы:
  refresh  (по умолчанию) открывает youtube.com в headless Chrome с постоянным профилем,
           чтобы браузер сам обновил (ротировал) cookies, затем сохраняет в БД только
           домены YouTube/Google. Если аккаунт не авторизован — БД не трогает.
  login    запускает Chrome с удалённой отладкой (127.0.0.1:9222) для одноразового входа
           в отдельный аккаунт Google через SSH-туннель.
  filter   оставляет в БД только cookies YouTube/Google (без обращения к браузеру).

Платформа выбирается ключом --platform (youtube по умолчанию, tiktok).

Настройки берутся из переменных окружения (см. /etc/yt-cookies.env):
  YT_DATABASE_URL, YT_USER_ID, YT_PROFILE_DIR, YT_CHROME      — YouTube
  TT_USER_ID, TT_PROFILE_DIR                                  — TikTok (БД и Chrome общие)
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
CHROME = os.environ.get("YT_CHROME", "/usr/bin/google-chrome")
PLATFORM = "youtube"  # переопределяется в main() через --platform
LOGIN_DONE_FILE = "/tmp/yt-login.done"
BOT_ENV_FILE = os.environ.get("YT_BOT_ENV_FILE", "/srv/projects/Video_down/.env")
STATE_FILE = Path(os.environ.get("YT_STATE_FILE", "/var/lib/yt-cookies/state.json"))
RUN_DIR = Path(os.environ.get("YT_RUN_DIR", "/srv/projects/Video_down/run"))
REPEAT_ALERT_SECONDS = 24 * 3600  # о той же проблеме напоминаем не чаще раза в сутки

# Для YouTube нужны домены YouTube/Google (cookies остальных сервисов Google не храним).
_GOOGLE_EXACT = {"google.com", "www.google.com", "accounts.google.com"}

PLATFORMS: dict[str, dict] = {
    "youtube": {
        "label": "YouTube",
        "user_id_env": "YT_USER_ID",
        "profile_env": "YT_PROFILE_DIR",
        "profile_default": "/srv/chrome-youtube",
        "suffixes": ("youtube.com", "youtube-nocookie.com", "googlevideo.com", "ytimg.com"),
        "google": True,
        # Признак авторизованной сессии.
        "auth_cookies": {"SID", "__Secure-3PSID", "__Secure-1PSID", "SAPISID"},
        "url": "https://www.youtube.com/",
        "login_url": "https://accounts.google.com/ServiceLogin?service=youtube",
        "request_file": "refresh-cookies.request",
        "result_file": "refresh-cookies.result",
        "state_key": "youtube",
    },
    "tiktok": {
        "label": "TikTok",
        "user_id_env": "TT_USER_ID",
        "profile_env": "TT_PROFILE_DIR",
        "profile_default": "/srv/chrome-tiktok",
        "suffixes": ("tiktok.com", "tiktokv.com", "tiktokcdn.com", "tiktokcdn-us.com", "ttwstatic.com"),
        "google": False,
        "auth_cookies": {"sessionid", "sessionid_ss", "sid_tt"},
        "url": "https://www.tiktok.com/",
        "login_url": "https://www.tiktok.com/login",
        "request_file": "refresh-cookies-tiktok.request",
        "result_file": "refresh-cookies-tiktok.result",
        "state_key": "tiktok",
    },
}


def _cfg() -> dict:
    return PLATFORMS[PLATFORM]


def _user_id() -> int:
    return int(os.environ.get(_cfg()["user_id_env"], "0"))


def _profile_dir() -> str:
    return os.environ.get(_cfg()["profile_env"], _cfg()["profile_default"])


def _domain_ok(domain: str) -> bool:
    d = domain.lstrip(".").lower()
    cfg = _cfg()
    if any(d == x or d.endswith("." + x) for x in cfg["suffixes"]):
        return True
    if not cfg["google"]:
        return False
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
    if not DATABASE_URL or not _user_id():
        sys.exit(f"Не заданы YT_DATABASE_URL / {_cfg()['user_id_env']}")
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
            (_user_id(), PLATFORM, text),
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
    full = _load_state()
    # Старый формат (плоский словарь) относился к YouTube.
    if "failing" in full:
        full = {"youtube": full}
    key, label = _cfg()["state_key"], _cfg()["label"]
    state = full.get(key, {})
    now = time.time()
    if ok:
        if state.get("failing"):
            notify_admins(f"✅ Cookies {label} снова обновляются, проблема устранена.")
            full[key] = {"failing": False}
            _save_state(full)
        return
    if state.get("failing") and now - state.get("last_alert", 0) < REPEAT_ALERT_SECONDS:
        return
    if notify_admins(f"⚠️ Не удалось обновить cookies {label}: {problem}\nЗагрузки с {label} могут перестать работать."):
        full[key] = {"failing": True, "last_alert": now}
    else:
        full[key] = {"failing": True, "last_alert": state.get("last_alert", 0)}
    _save_state(full)


def cmd_filter() -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cookies_text FROM user_cookies_video_bot WHERE user_id=%s AND platform=%s",
            (_user_id(), PLATFORM),
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
            _profile_dir(), executable_path=CHROME, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(_cfg()["url"], wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)  # даём сайту выдать обновлённые сессионные cookies
            cookies = ctx.cookies()
        finally:
            ctx.close()
    names = {c["name"] for c in cookies if _domain_ok(c["domain"])}
    if not names & _cfg()["auth_cookies"]:
        print("Аккаунт не авторизован в профиле — БД не изменена (запустите login)", file=sys.stderr)
        report_result(False, f"аккаунт {_cfg()['label']} в профиле Chrome больше не авторизован, нужен повторный вход (yt_cookies.py --platform {PLATFORM} login).")
        return 2
    text, kept = to_netscape(cookies)
    save(text)
    print(f"cookies обновлены: {kept} шт.")
    report_result(True)
    return 0


def _answer_worker(ok: bool) -> None:
    """Если воркер ждёт обновления (файл-запрос в RUN_DIR), снимаем запрос и сообщаем результат."""
    request = RUN_DIR / _cfg()["request_file"]
    try:
        request.unlink(missing_ok=True)
        if RUN_DIR.is_dir():
            (RUN_DIR / _cfg()["result_file"]).write_text("ok" if ok else "fail")
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
            _profile_dir(), executable_path=CHROME, headless=True,
            args=["--no-sandbox", "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
                  "--remote-allow-origins=*", "--window-size=1280,900"],
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_cfg()["login_url"], timeout=60000)
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
    ap.add_argument("--platform", default="youtube", choices=sorted(PLATFORMS))
    args = ap.parse_args()
    global PLATFORM
    PLATFORM = args.platform
    return {"refresh": cmd_refresh, "login": cmd_login, "filter": cmd_filter}[args.mode]()


if __name__ == "__main__":
    sys.exit(main())
