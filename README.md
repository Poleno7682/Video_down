<div align="center">

# 🎬 Video Downloader Bot

**Telegram-бот для скачивания публичных видео по ссылке**

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-blue?logo=telegram)](https://docs.aiogram.dev)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://docs.docker.com/compose/)
[![Tests](https://img.shields.io/badge/Tests-676%20passed-brightgreen?logo=pytest)](tests/)
[![Coverage](https://img.shields.io/badge/Coverage-high-brightgreen)](tests/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

Отправь боту ссылку — получи видео прямо в чат.  
Поддерживает YouTube, VK, TikTok, Instagram, Facebook, **rezka.ag** и [1000+ других сайтов](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

</div>

---

## 📋 Содержание

- [Ключевые возможности](#-ключевые-возможности)
- [Стек технологий](#-стек-технологий)
- [Быстрый старт (автоустановка)](#-быстрый-старт-автоустановка)
- [Ручная установка](#-ручная-установка)
- [SSL-сертификат и автоперевыпуск](#-ssl-сертификат-и-автоперевыпуск)
- [Управление службой](#-управление-службой)
- [Команды бота](#-команды-бота)
- [Управление доступом](#-управление-доступом)
- [Рассылка администратора](#-рассылка-администратора)
- [Cookie-файлы и Google-аккаунт](#-cookie-файлы-и-google-аккаунт)
- [rezka.ag: антибот-обход и выбор серий](#-rezkaag-антибот-обход-и-выбор-серий)
- [Пул прокси и VPN-фоллбек](#-пул-прокси-и-vpn-фоллбек)
- [Local Bot API (файлы больше 50 МБ)](#-local-bot-api-файлы-больше-50-мб)
- [Переменные окружения](#-переменные-окружения)
- [Тесты](#-тесты)
- [Структура проекта](#-структура-проекта)
- [Рекомендации для продакшена](#-рекомендации-для-продакшена)

---

## ✨ Ключевые возможности

### 📥 Загрузка видео
- **1000+ сайтов** — YouTube, VK, TikTok, Instagram, Facebook, Twitter/X, Twitch, Dailymotion, **rezka.ag** и другие через yt-dlp
- **Выбор качества** — 360p / 480p / 720p / 1080p / наилучшее / только аудио
- **Кэш `file_id`** — если видео с тем же качеством уже скачивалось, бот мгновенно отправляет его повторно из кэша Telegram без повторной загрузки
- **Дедупликация загрузок** — параллельные запросы на один и тот же `url_hash + качество` не скачиваются дважды: второй запрос ждёт результат первого вместо повторной загрузки
- **Автоматические повторные попытки** — yt-dlp: 3 попытки на запрос, 5 на фрагмент; при HTTP 403/410/429 или гео-блокировке — повтор с браузерными заголовками (User-Agent, Accept-Language, geo-bypass); при таймауте соединения — повтор с увеличенным сокет-таймаутом
- **Кодек-осознанный выбор формата** — приоритет H.264 → HEVC → любой другой кодек; VP9/AV1 избегаются намеренно (в клиентах Telegram такие видео иногда рендерятся статичным кадром); для Facebook добавлен фоллбек на прогрессивный `hd`-формат (чистый H.264+AAC вместо AV1 DASH-дорожек без аудио)
- **Автотранскодирование** — если скачанный файл всё же в VP9/AV1, он перекодируется в H.264 через ffmpeg перед отправкой, с живым прогрессом пользователю
- **Валидация скачанного файла** — ffprobe проверяет наличие видео/аудио-дорожек, короткий тестовый decode через ffmpeg отсекает битые/несовместимые кодеки до отправки в Telegram
- **Автосжатие под лимит Telegram** — если файл больше лимита, несколько попыток ffmpeg-компрессии с понижением разрешения/битрейта вместо отказа
- **Проверка на активную трансляцию** — ссылка на незавершённый live-эфир отклоняется до начала загрузки (кроме rezka — там нет живых трансляций в принципе)
- **Вжигание субтитров** (опционально, `EMBED_SUBTITLES=true`) — оригинальные или автосгенерированные субтитры (ru/en) вшиваются в кадр видео через ffmpeg
- **JS-рантайм Deno** в образе — нужен yt-dlp для обхода плеер-челленджей YouTube
- **Постоянная подпись** под каждым видео — берётся из файла `caption.txt`, меняется на лету без пересборки
- **Персональные cookie** для каждого пользователя, включая автоматический вход через Google (см. [ниже](#-cookie-файлы-и-google-аккаунт))

### 🎞️ rezka.ag: полноценный обход антибота
- **Обход Anubis** (proof-of-work JS-челлендж «Проверяем, что вы не бот!») — реальный headless Chromium через Playwright решает челлендж, обычный HTTP-запрос или сервисы вроде FlareSolverr здесь бессильны
- **Кэш решённых cookie** в Redis на 6 часов на домен — челлендж решается не на каждый запрос
- **Автораспознавание зеркал** — rezka-ua, rezka-ag.net и любые другие домены с «rezka» в названии
- **Выбор озвучки/сезона/серии** через inline-меню с навигацией «назад», и кнопка «скачать весь сезон целиком»
- **Устойчивость к «сессия истекла»** — мёрж `Set-Cookie` со страницы фильма и повтор `getStream` при протухшей сессии
- **Отдельный маршрут сети** — не использует общий пул прокси YouTube (он для этого слишком ненадёжен); пробует прямое соединение, затем один выделенный VPN-прокси (см. [ниже](#-пул-прокси-и-vpn-фоллбек))

### 📢 Рассылка администратора
- Режим рассылки: всё, что админ отправит в чат (текст, фото, GIF, видео, музыка, эмодзи), уходит всем пользователям через `copy_message`
- **Inline-кнопки** в рассылке — синтаксис `Текст | https://ссылка` после строки `---`
- **Таймер-защита** — режим авто-выключается через 5 минут без активности; каждая рассылка сбрасывает таймер; есть inline-кнопка отмены
- Сохраняются **все пользователи**, кто хоть раз запускал бота — они и получают рассылку

### 🔐 Управление доступом
- **Три режима**: публичный бот / статичный список в `.env` / динамический список доверенных через `/adduser`
- **Панель администратора** — инлайн-меню со статусом бота, режимом доступа, кнопкой мгновенного включения/выключения бота для всех
- **Динамические лимиты** — rate limit, дневные лимиты и лимиты очереди редактируются прямо в `/admin` (текстовым вводом нового значения или сбросом к значению из `.env`) без перезапуска бота
- **Администратор всегда имеет доступ** — глобальное отключение бота на него не распространяется

### 🌐 Пул прокси для yt-dlp
- **Управление через бота** (`/addproxy`, `/delproxy`, `/listproxies`) — не нужно лезть в `.env` и перезапускать контейнеры
- **Живая проверка** — каждый добавленный прокси реально пробуется на YouTube перед сохранением, битые отклоняются сразу
- **Массовая загрузка** — можно прислать `.txt`-файл (до 500 строк) со списком прокси, с прогрессом и итоговым отчётом (добавлено/дубликат/не прошёл проверку)
- **Приоритет по надёжности** — прокси перебираются от наименее отказавшего к наиболее отказавшему (`failure_count`)
- Пул используется **только для YouTube**; при пустом пуле — фоллбек на `YTDLP_PROXY` из `.env`

### 🔑 Google-аккаунт для YouTube
- **`/link_google`** — device-flow авторизация Google (как на телевизорах): бот даёт код, пользователь вводит его на `google.com/device` с любого устройства
- Полученные cookie сохраняются как персональные YouTube-cookie пользователя — не нужно вручную экспортировать `cookies.txt` из браузера
- **Автообновление** — если при скачивании YouTube требует новые cookie, бот сам обновляет их через сохранённый refresh-токен (с защитой от повторов раз в 5 минут)
- **`/unlink_google`** — отвязка аккаунта, удаление токена и сгенерированных cookie

### 🛡️ Антиспам и защита
- **Rate limiter** — скользящее временное окно с автоматическим баном нарушителей
- **Атомарные Lua-скрипты** в Redis — счётчики без race condition при параллельных запросах
- **Дневной лимит** запросов на пользователя
- **Лимит активных задач** на пользователя и глобально
- **Nginx rate limit** на webhook-эндпоинт (30 req/s, burst 60)

### 🔒 Безопасность
- Нет сырого SQL — весь доступ к БД через SQLAlchemy ORM и параметризованные запросы
- Webhook защищён заголовком `X-Telegram-Bot-Api-Secret-Token`
- HTTPS через Let's Encrypt с автоматическим перевыпуском сертификата
- Файл `.env` создаётся с правами `600` (только владелец)
- `WEBHOOK_SECRET` генерируется автоматически (`openssl rand -hex 32`)

### 🏗️ Архитектура и надёжность
- **Два режима работы** — `webhook` (нужен HTTPS-домен) и `polling` (работает за NAT/без домена, см. `BOT_MODE`)
- **Celery-воркер** — задачи выполняются асинхронно, бот не блокируется во время скачивания
- **Прогресс-хук** — пользователь видит обновления статуса в реальном времени во время загрузки и транскодирования
- **Часовая автоочистка** — Celery Beat раз в час подчищает `downloads/active/`, если воркер упал и не убрал за собой файл
- **Alembic-миграции** применяются автоматически при каждом запуске бота
- **ISP-сегрегированные репозитории** — `UserRepository`, `CookieRepository`, `VideoRepository`, `RequestRepository`, `ProxyRepository`, `GoogleTokenRepository` — каждый caller получает только нужный интерфейс
- **AccessMiddleware + AdminFilter** — доступ и проверка администратора вынесены в aiogram middleware и фильтр; обработчики не содержат guard-кода
- **Local Bot API** (опционально) — поднимает лимит на файл с 50 МБ до 2000 МБ
- **Healthcheck** — Docker Compose проверяет готовность PostgreSQL и Redis перед стартом бота
- **systemd-служба** — автозапуск всего стека при перезагрузке сервера

### ⚙️ Автоустановка
- Один скрипт настраивает всё с нуля: зависимости, Docker, SSL, nginx, `.env`, systemd
- Умная проверка зависимостей — устанавливает только то, чего нет
- Docker Engine устанавливается через **официальный репозиторий** Docker Inc., а не устаревший пакет Ubuntu

---

## 🛠 Стек технологий

| Слой | Технология |
|---|---|
| Telegram Bot Framework | [aiogram 3.x](https://docs.aiogram.dev) — webhook, inline-клавиатуры, scope команд |
| HTTP-сервер | [aiohttp](https://docs.aiohttp.org) — обработка webhook-запросов |
| Очередь задач | [Celery 5](https://docs.celeryq.dev) + Redis — асинхронное скачивание, Celery Beat — периодическая очистка |
| База данных | PostgreSQL 16 + [SQLAlchemy 2.0](https://docs.sqlalchemy.org) ORM |
| Миграции БД | [Alembic](https://alembic.sqlalchemy.org) — автоприменение при старте |
| Скачивание видео | [yt-dlp](https://github.com/yt-dlp/yt-dlp) + ffmpeg — 1000+ сайтов |
| rezka.ag | [HdRezkaApi](https://pypi.org/project/HdRezkaApi/) + [Playwright](https://playwright.dev) (headless Chromium) — резолв потока и обход антибота Anubis |
| Google OAuth | Device Authorization Flow — авторизация YouTube без пароля в боте |
| Кэш / блокировки | [Redis](https://redis.io) — rate limiting, file_id кэш, дедупликация, сессии выбора серий |
| Конфигурация | [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — типизированный `.env` |
| Local Bot API | [aiogram/telegram-bot-api](https://github.com/tdlib/telegram-bot-api) — опциональный лимит файла до 2000 МБ |
| VPN-фоллбек | [gluetun](https://github.com/qdm12/gluetun) (OpenVPN-клиент) — последний резервный маршрут для rezka |
| Обратный прокси | Nginx 1.27 — HTTPS терминация, rate limit |
| SSL | [Let's Encrypt](https://letsencrypt.org) + certbot — выпуск и автоперевыпуск |
| Контейнеры | Docker Compose — сервисы с healthcheck, часть — опциональные профили |
| Системная служба | systemd — `video-bot.service`, автостарт при перезагрузке |
| Тестирование | pytest + pytest-asyncio + pytest-cov + pytest-mock |

---

## 🚀 Быстрый старт (автоустановка)

> **Требования:** чистый Ubuntu 22.04 / 24.04, домен с A-записью на IP сервера, доступ по SSH от root.

Скрипт `setup.sh` настраивает всё с нуля за один запуск. Он проведёт вас через все шаги интерактивно.

### Шаг 1 — Подключитесь к серверу и клонируйте репозиторий

```bash
ssh root@ваш-сервер

git clone https://github.com/Poleno7682/Video_down.git
cd Video_down
```

### Шаг 2 — Запустите скрипт установки

```bash
sudo bash scripts/setup.sh
```

Скрипт задаст вам несколько вопросов:

| Вопрос | Пример |
|---|---|
| Токен бота (от @BotFather) | `123456789:AABBccDDeeff...` |
| Хост PostgreSQL | `postgres` (внутри Docker — оставьте по умолчанию) |
| Название базы данных | `video_bot` |
| Пользователь БД | `video_bot` |
| Пароль БД | `MySuperPassword123` |
| Домен сервера | `bot.example.com` |
| Email для Let's Encrypt | `admin@example.com` |
| Telegram ID администратора | `123456789` (узнать через @userinfobot) |

### Что делает скрипт автоматически

```
1. Проверяет наличие Docker, certbot, curl, openssl, python3
   └── Устанавливает отсутствующее (Docker — через официальный репозиторий)

2. Запрашивает SSL-сертификат Let's Encrypt (certbot --standalone)
   └── Копирует .pem файлы в nginx/certs/

3. Создаёт скрипт автоперевыпуска scripts/renew_cert.sh
   └── Регистрирует cron-задание на ежедневный запуск в 03:15

4. Генерирует nginx/video-bot.conf с вашим доменом

5. Генерирует .env с токеном, паролями и случайным WEBHOOK_SECRET

6. Создаёт и включает systemd-службу /etc/systemd/system/video-bot.service

7. Собирает Docker-образы (pip install requirements.txt внутри контейнера)

8. Запускает стек через systemctl start video-bot

9. Проверяет доступность бота через GET /health
```

### Шаг 3 — Убедитесь что всё работает

```bash
systemctl status video-bot
docker compose ps
curl https://bot.example.com/health
```

Ожидаемый ответ health-check:
```json
{"status": "ok"}
```

---

## 🔧 Ручная установка

Если хотите настроить всё вручную или разобраться в деталях.

### Шаг 1 — Установите Docker Engine

```bash
# Добавьте официальный репозиторий Docker
apt-get update
apt-get install -y ca-certificates curl gnupg

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list

# Установите Docker CE и плагин Compose
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker
```

### Шаг 2 — Клонируйте репозиторий

```bash
git clone https://github.com/Poleno7682/Video_down.git
cd Video_down
```

### Шаг 3 — Настройте переменные окружения

```bash
cp .env.example .env
nano .env
```

Обязательно заполните:

```env
BOT_TOKEN=123456789:ваш_токен_от_BotFather
WEBHOOK_BASE_URL=https://bot.example.com
WEBHOOK_SECRET=сгенерируйте_через_openssl_rand_hex_32
POSTGRES_PASSWORD=надёжный_пароль
DATABASE_URL=postgresql+psycopg2://video_bot:надёжный_пароль@postgres:5432/video_bot
ADMIN_USERS=ваш_telegram_id
```

### Шаг 4 — Получите SSL-сертификат

```bash
apt-get install -y certbot

# certbot использует порт 80 — убедитесь что он свободен
certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email ваш@email.com \
    -d bot.example.com

# Скопируйте сертификаты в папку проекта
mkdir -p nginx/certs
cp /etc/letsencrypt/live/bot.example.com/fullchain.pem nginx/certs/
cp /etc/letsencrypt/live/bot.example.com/privkey.pem   nginx/certs/
chmod 644 nginx/certs/*.pem
```

### Шаг 5 — Укажите домен в конфиге Nginx

Отредактируйте `nginx/video-bot.conf` — замените `your-domain.com` на ваш домен:

```bash
sed -i 's/your-domain.com/bot.example.com/g' nginx/video-bot.conf
```

### Шаг 6 — Создайте системную службу (опционально, но рекомендуется)

```bash
cat > /etc/systemd/system/video-bot.service <<EOF
[Unit]
Description=Video Bot (Docker Compose)
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$(pwd)
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable video-bot
```

### Шаг 7 — Соберите образы и запустите

```bash
docker compose build
systemctl start video-bot

# Или без systemd:
docker compose up -d
```

Если нужен webhook-режим (Nginx + SSL), rezka VPN-фоллбек или Local Bot API — добавьте соответствующие профили (см. [Пул прокси и VPN-фоллбек](#-пул-прокси-и-vpn-фоллбек) и [Local Bot API](#-local-bot-api-файлы-больше-50-мб)):

```bash
docker compose --profile webhook up -d
docker compose --profile webhook --profile rezka-vpn --profile local-bot-api up -d
```

### Шаг 8 — Проверьте логи

```bash
docker compose logs -f bot
docker compose logs -f worker
```

---

## 🔐 SSL-сертификат и автоперевыпуск

### Первичный выпуск сертификата

При автоустановке скрипт запрашивает сертификат командой:

```bash
certbot certonly --standalone --non-interactive --agree-tos \
    --email ваш@email.com -d бот.example.com
```

Режим `--standalone` — certbot временно поднимает HTTP-сервер на порту 80 для верификации домена через ACME-challenge. Nginx в это время ещё не запущен, поэтому порт свободен.

После выпуска сертификаты копируются в папку проекта и монтируются в контейнер Nginx:

```
/etc/letsencrypt/live/example.com/
    fullchain.pem  →  nginx/certs/fullchain.pem
    privkey.pem    →  nginx/certs/privkey.pem
```

### Автоматический перевыпуск

Скрипт создаёт два объекта для автоперевыпуска:

**1. `scripts/renew_cert.sh`** — скрипт обновления:

```bash
certbot renew --standalone \
    --pre-hook  "docker compose stop nginx"   # освобождает порт 80
    --post-hook "cp новые_сертификаты nginx/certs/ && docker compose start nginx"
```

Алгоритм работы:
- `--pre-hook` останавливает контейнер Nginx → порт 80 свободен
- certbot проверяет срок действия (обновляет только если до истечения < 30 дней)
- `--post-hook` копирует новые `.pem` в папку проекта и запускает Nginx обратно

**2. `/etc/cron.d/video-bot-certbot`** — cron-задание:

```
15 3 * * * root /path/to/scripts/renew_cert.sh
```

Запускается ежедневно в **03:15**. Реальное обновление происходит раз в ~60 дней (Let's Encrypt выдаёт сертификаты на 90 дней, certbot обновляет за 30 дней до истечения).

Лог перевыпуска пишется в `logs/certbot.log`.

---

## ⚙️ Управление службой

### Через systemd (рекомендуется)

```bash
systemctl status video-bot      # статус всего стека
systemctl start video-bot       # запуск
systemctl stop video-bot        # остановка (docker compose down)
systemctl restart video-bot     # перезапуск
systemctl enable video-bot      # включить автостарт при перезагрузке
systemctl disable video-bot     # выключить автостарт

journalctl -u video-bot -f      # логи запуска и остановки службы
journalctl -u video-bot --since "1 hour ago"
```

### Через Docker Compose (для работы с отдельными контейнерами)

```bash
docker compose ps                       # статус всех контейнеров
docker compose logs -f bot              # логи бота в реальном времени
docker compose logs -f worker           # логи Celery-воркера
docker compose logs -f nginx            # логи Nginx
docker compose logs -f postgres         # логи PostgreSQL

docker compose restart bot              # перезапуск только бота
docker compose exec bot python -c "..."  # выполнить команду в контейнере

docker compose up -d --scale worker=3  # запустить 3 воркера
```

---

## 🤖 Команды бота

### Пользовательские команды

| Команда | Описание |
|---|---|
| `/start`, `/help` | Справка: как пользоваться ботом |
| `/quality` | Выбрать качество видео через инлайн-меню |
| `/status` | Статус очереди: активные задачи и дневной счётчик |
| `/cookies` | Инструкция и статус личных cookie; загрузка файлом |
| `/delcookies <platform>` | Удалить свои cookie для платформы (`youtube`/`instagram`/`tiktok`/`facebook`) |
| `/link_google` | Привязать Google-аккаунт — автоматические cookie для YouTube без ручного экспорта |
| `/unlink_google` | Отвязать Google-аккаунт и удалить сохранённые cookie/токен |
| *(любая ссылка)* | Скачать видео. Ссылку можно прислать отдельным сообщением или вставить в подпись к медиа. Для rezka.ag бот сначала предложит выбрать озвучку/сезон/серию |
| *(файл `<platform>.txt`)* | Загрузить личные cookie (имя файла задаёт платформу) |

### Команды администратора

| Команда | Описание |
|---|---|
| `/admin` | Панель администратора: статус бота, режим доступа, лимиты, кнопки вкл/выкл и рассылки |
| `/broadcast` | Включить режим рассылки всем пользователям |
| `/adduser <id>` | Добавить пользователя в доверенные по Telegram ID |
| `/removeuser <id>` | Убрать пользователя из доверенных |
| `/listusers` | Список всех доверенных пользователей |
| `/addproxy` | Добавить прокси для yt-dlp (спросит схему, проверит на YouTube перед сохранением; можно прислать `.txt` списком) |
| `/delproxy <id>` | Удалить прокси из пула по ID |
| `/listproxies` | Список прокси в пуле со счётчиком отказов каждого |

> Узнать свой Telegram ID можно через бота [@userinfobot](https://t.me/userinfobot).

Меню команд в Telegram настраивается автоматически: обычные пользователи видят только пользовательские команды, администратор — расширенный список.

---

## 🔑 Управление доступом

Бот поддерживает три режима — переключение происходит автоматически по приоритету:

```
Администратор (ADMIN_USERS)  →  всегда разрешён
       ↓
Bot disabled (Redis key)     →  блокирует всех не-администраторов
       ↓
ALLOWED_USERS в .env         →  статичный список разрешённых ID
       ↓
Доверенные (/adduser)        →  динамический список через Redis
       ↓
Публичный бот                →  доступен всем (если ни одного из вышеперечисленного)
```

| Режим | Как включить | Кто имеет доступ |
|---|---|---|
| **Публичный** | `ALLOWED_USERS` пуст, список `/adduser` пуст | Все пользователи |
| **Статичный список** | Заполнить `ALLOWED_USERS=111,222,333` в `.env` | Только перечисленные ID |
| **Доверенные пользователи** | Добавить через `/adduser <id>` | Только добавленные администратором |
| **Бот выключен** | Нажать кнопку в `/admin` | Только администраторы |

Переключить кнопкой "🔴 Выключить бот для всех" / "🟢 Включить бот для всех" в панели `/admin`. Там же — редактирование лимитов (`RATE_LIMIT_*`, `USER_DAILY_LIMIT`, `USER_QUEUE_LIMIT`, `GLOBAL_QUEUE_LIMIT` и др.): выбираете лимит, присылаете новое число или `сброс`, чтобы вернуть значение из `.env` — без перезапуска бота.

---

## 📢 Рассылка администратора

Бот умеет рассылать сообщение всем пользователям, которые когда-либо запускали его (они сохраняются в таблице `users` при `/start`).

**Как пользоваться:**
1. Откройте `/admin` и нажмите «📢 Рассылка» (или команду `/broadcast`).
2. Бот включит режим рассылки и покажет inline-кнопку «❌ Отменить рассылку».
3. Отправьте сообщение — оно будет разослано всем. По завершении придёт отчёт `доставлено / не доставлено / всего`.

**Что поддерживается:** текст, эмодзи, фото, GIF, видео, музыка, форматирование — всё копируется через Telegram `copy_message`.

**Inline-кнопки в рассылке.** Добавьте в конец сообщения строку-разделитель `---`, а далее по одной кнопке в строке:

```
Привет! Вышло обновление 🎉
---
Открыть сайт | https://example.com
Наш канал | https://t.me/example
```

**Таймер-защита.** Режим автоматически выключается через `BROADCAST_TIMEOUT_SECONDS` (по умолчанию 5 минут) без активности. Каждая отправка сбрасывает таймер. Реализовано через TTL ключа в Redis — фоновых задач не требуется.

---

## 🍪 Cookie-файлы и Google-аккаунт

Некоторые сайты требуют cookie авторизованного аккаунта (приватные/возрастные видео, а YouTube на VPS почти всегда — иначе ошибка «Sign in to confirm you're not a bot»). Поддерживаются три способа получить cookie.

### Google-аккаунт через `/link_google` (проще всего для YouTube)

1. Отправьте боту `/link_google` — он пришлёт ссылку `google.com/device` и короткий код.
2. Откройте ссылку с любого устройства, войдите в Google-аккаунт и подтвердите код.
3. Бот сам подхватит одобрение (опрашивает Google в фоне) и сгенерирует YouTube-cookie — ничего экспортировать вручную не нужно.
4. Если при скачивании YouTube всё же потребует новые cookie, бот **автоматически обновит их** через сохранённый refresh-токен (не чаще раза в 5 минут, чтобы не зациклиться).
5. `/unlink_google` — отвязать аккаунт и удалить токен и сгенерированные cookie.

Ограничение: этим способом нельзя получить cookie для приватных/возрастных видео (нужны более полные cookie реального браузера) — для них по-прежнему нужен ручной `youtube.txt` через `/cookies`.

### Персональные cookie вручную

Каждый пользователь может вместо этого загрузить свои cookie прямо боту — они хранятся в PostgreSQL (таблица `user_cookies`) и доступны воркеру без общих файловых маунтов. При скачивании воркер материализует временный cookie-файл и удаляет его после. Загрузка через `/link_google` пишет в тот же самый слот — последний использованный способ побеждает.

1. Экспортируйте cookie в формате **Netscape** (`cookies.txt`) из браузера, где вы вошли в аккаунт (расширение [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/), или `yt-dlp --cookies-from-browser chrome --cookies youtube.txt`).
2. Переименуйте файл по платформе: `youtube.txt`, `instagram.txt`, `tiktok.txt` или `facebook.txt`.
3. Просто пришлите файл боту. Проверить статус — `/cookies`, удалить — `/delcookies youtube`.

### Глобальные cookie (fallback)

Можно положить общие cookie-файлы в папку `cookies/` — они используются, если у пользователя нет личных:

```
cookies/
├── youtube.txt
├── facebook.txt
├── instagram.txt
└── tiktok.txt
```

**Приоритет:** личные cookie пользователя (включая полученные через `/link_google`) → глобальный файл платформы → без cookie.

**Отключить cookies полностью:**
```env
USE_COOKIES=false
```

---

## 🎞️ rezka.ag: антибот-обход и выбор серий

rezka.ag/hdrezka.* — популярный видеосервис (фильмы, сериалы) на русском, прячущийся за антибот-проверкой **Anubis**: JS-задача с proof-of-work, которую нельзя решить обычным HTTP-запросом или сервисами вроде FlareSolverr (они такой тип челленджа не распознают). Бот решает её реальным headless-браузером.

**Как это работает:**

1. Ссылка на rezka.ag (любое зеркало — rezka-ua, rezka-ag.net и т.д.) распознаётся отдельно от остальных сайтов и не идёт по обычному пути yt-dlp.
2. При первом обращении к домену за сессию поднимается headless Chromium (Playwright), который проходит проверку Anubis; решённые cookie кэшируются в Redis на 6 часов, чтобы не решать челлендж на каждый запрос.
3. Бот запрашивает у страницы список озвучек, сезонов и серий ([HdRezkaApi](https://pypi.org/project/HdRezkaApi/)) и показывает inline-меню выбора — с кнопками «назад» на каждом шаге и кнопкой «скачать весь сезон».
4. Если сессия «протухла» между шагами (это же кэшируемые cookie), бот сам обновляет их и повторяет запрос — без ошибки для пользователя.
5. Итоговый прямой URL видео скачивается: сначала напрямую (CDN rezka обычно не блокируется по IP так, как YouTube), при неудаче — через один резервный VPN-прокси, если он настроен (см. ниже). Общий пул прокси YouTube здесь не используется — Anubis-cookie привязаны к IP, решившему челлендж, так что проксирование сломало бы уже пройденную проверку.

**Включение:**

```env
REZKA_ANTIBOT_BYPASS=true
```

По умолчанию выключено — headless Chromium заметно тяжелее по CPU/RAM, чем обычная загрузка. Требует `playwright install chromium` в образе (уже настроено в `Dockerfile`). Без этого флага ссылки на rezka.ag будут падать с понятной ошибкой «антибот-страница», остальные сайты не затронуты.

---

## 🌐 Пул прокси и VPN-фоллбек

### Пул прокси для YouTube (`/addproxy`)

Устойчивость к антибот-блокировкам YouTube по IP датацентра решается пулом прокси, которым управляет администратор прямо из бота — без правки `.env` и перезапуска:

- **`/addproxy`** — выбираете схему (`socks5://`, `socks5h://`, `socks4://`, `http://`, `https://`) через кнопки, затем присылаете строку `IP:PORT`, `IP:PORT@LOGIN:PASS` (или полный URL) — либо сразу `.txt`-файл со списком (до 500 строк, `#`-комментарии разрешены). Каждый прокси **реально проверяется на YouTube** перед сохранением; дубликаты отбрасываются.
- **`/listproxies`** — список с ID и счётчиком последовательных отказов; прокси перебираются от наименее отказавшего.
- **`/delproxy <id>`** — удалить прокси из пула.
- Если пул пуст — используется `YTDLP_PROXY` из `.env` как единственный прокси.

Этот пул используется **только для YouTube** — на другие сайты (кроме rezka, у которого свой маршрут) прокси не распространяются, чтобы не добавлять им лишних точек отказа.

### VPN-фоллбек только для rezka (`vpn` контейнер)

Иногда CDN rezka (например, `stream.voidboost.cc`) полностью не отвечает с конкретных диапазонов IP VPS. Для этого случая есть отдельный, никак не связанный с пулом YouTube маршрут — через контейнер `vpn` ([gluetun](https://github.com/qdm12/gluetun), OpenVPN-клиент), пробуемый последним, уже после прямого подключения:

1. Получите `.ovpn`-файл от вашего VPN-провайдера и положите его как `vpn/vpn.ovpn` (см. `vpn/README.md`; сам файл в git не попадает — уже в `.gitignore`).
2. В `.env` укажите:
   ```env
   VPN_OPENVPN_USER=...
   VPN_OPENVPN_PASSWORD=...
   REZKA_VPN_PROXY_URL=http://vpn:8888
   ```
3. Запустите вместе с остальным стеком:
   ```bash
   docker compose --profile rezka-vpn up -d
   ```

Если `REZKA_VPN_PROXY_URL` пуст — ничего не меняется, контейнер `vpn` не обязателен и не запускается.

---

## 📦 Local Bot API (файлы больше 50 МБ)

Обычный облачный Telegram Bot API ограничивает загрузку/скачивание файлов **50 МБ**. Чтобы поднять лимит до **2000 МБ**, можно поднять собственный [Local Bot API сервер](https://core.telegram.org/bots/api#using-a-local-bot-api-server):

1. Получите `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` на [my.telegram.org/apps](https://my.telegram.org/apps).
2. В `.env`:
   ```env
   USE_LOCAL_BOT_API=true
   MAX_FILE_MB=2000
   TELEGRAM_API_ID=...
   TELEGRAM_API_HASH=...
   ```
3. Запустите дополнительный сервис:
   ```bash
   docker compose --profile local-bot-api up -d
   ```

Контейнеры `bot` и `telegram-bot-api` шарят общий том `telegram_bot_api_data` — Local Bot API отдаёт загруженные файлы (cookies.txt, списки прокси) как путь на диске, а не по HTTP, поэтому без общего тома загрузка файлов боту будет падать с `FileNotFoundError`, даже если сам бот работает.

---

## ⚙️ Переменные окружения

### Telegram

| Переменная | Обязательна | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен от @BotFather |
| `BOT_MODE` | | `polling` (за NAT, без домена) или `webhook` (нужен HTTPS-домен) |
| `WEBHOOK_BASE_URL` | webhook | Публичный URL (`https://bot.example.com`) — только для webhook |
| `WEBHOOK_PATH` | | Путь вебхука (по умолчанию `/telegram/webhook`) |
| `WEBHOOK_SECRET` | webhook | Случайная строка для защиты вебхука — только для webhook |

### База данных и очередь

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `DATABASE_URL` | ✅ | Строка подключения к PostgreSQL |
| `POSTGRES_PASSWORD` | ✅ | Пароль PostgreSQL |
| `REDIS_URL` | `redis://redis:6379/0` | URL Redis |
| `CELERY_BROKER_URL` | `redis://redis:6379/1` | Брокер Celery |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/2` | Бэкенд результатов |

### Доступ

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `ADMIN_USERS` | — | Telegram ID администраторов через запятую |
| `ALLOWED_USERS` | — | Статичный белый список ID (пусто = публичный/доверенные) |

### Антиспам

| Переменная | По умолчанию | Описание |
|---|---|---|
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Окно rate limiter (секунды) |
| `RATE_LIMIT_MAX_MESSAGES` | `8` | Макс. запросов в окне |
| `BAN_SECONDS` | `600` | Длительность бана (10 минут) |
| `USER_DAILY_LIMIT` | `50` | Макс. запросов в день на пользователя |
| `USER_QUEUE_LIMIT` | `3` | Макс. одновременных задач на пользователя |
| `GLOBAL_QUEUE_LIMIT` | `50` | Глобальный лимит очереди |

> Эти лимиты можно менять «на лету» в `/admin` без перезапуска бота — значения из `.env` служат лишь дефолтом и целью для сброса.

### Загрузка

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DEFAULT_QUALITY` | `720p` | Качество по умолчанию |
| `MAX_FILE_MB` | `50` | Макс. размер файла (50 МБ — лимит обычного Bot API; до 2000 при `USE_LOCAL_BOT_API=true`) |
| `DOWNLOAD_TIMEOUT_SECONDS` | `900` | Таймаут скачивания (15 минут) |
| `MAX_ACTIVE_DOWNLOADS_PER_USER` | `1` | Макс. параллельных загрузок на пользователя |
| `MAX_DOWNLOAD_DURATION_SECONDS` | `1800` | Макс. длительность видео (30 минут) |
| `STALE_FILE_MAX_AGE_HOURS` | `24` | Возраст файла в `downloads/active/`, после которого его подчистит часовая автоочистка (страховка на случай упавшего воркера) |

### Прокси и rezka

| Переменная | По умолчанию | Описание |
|---|---|---|
| `YTDLP_PROXY` | — | Резервный прокси для YouTube, если пул `/addproxy` пуст |
| `REZKA_VPN_PROXY_URL` | — | Резервный VPN-прокси только для rezka (см. [VPN-фоллбек](#-пул-прокси-и-vpn-фоллбек)); пусто = выключено |
| `VPN_OPENVPN_USER` / `VPN_OPENVPN_PASSWORD` | — | Логин/пароль для `.ovpn`-конфига контейнера `vpn` |
| `REZKA_ANTIBOT_BYPASS` | `false` | Включить обход антибота Anubis через headless Chromium для rezka.ag |

### Local Bot API

| Переменная | По умолчанию | Описание |
|---|---|---|
| `USE_LOCAL_BOT_API` | `false` | Использовать self-hosted Local Bot API вместо api.telegram.org |
| `LOCAL_BOT_API_URL` | `http://telegram-bot-api:8081` | Адрес Local Bot API сервера |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | — | Из [my.telegram.org/apps](https://my.telegram.org/apps), нужны только для Local Bot API |

### Cookie

| Переменная | По умолчанию | Описание |
|---|---|---|
| `USE_COOKIES` | `true` | Включить использование cookie (личных и глобальных) |
| `YOUTUBE_COOKIES_FILE` | `/app/cookies/youtube.txt` | Глобальный cookie-файл YouTube |
| `FACEBOOK_COOKIES_FILE` | `/app/cookies/facebook.txt` | Глобальный cookie-файл Facebook |
| `INSTAGRAM_COOKIES_FILE` | `/app/cookies/instagram.txt` | Глобальный cookie-файл Instagram |
| `TIKTOK_COOKIES_FILE` | `/app/cookies/tiktok.txt` | Глобальный cookie-файл TikTok |

### Кэш, подпись и рассылка

| Переменная | По умолчанию | Описание |
|---|---|---|
| `CACHE_TTL_HOURS` | `168` | Срок хранения `file_id` в кэше (7 дней) |
| `DELETE_LOCAL_FILE_AFTER_TELEGRAM_CACHE` | `true` | Удалять локальный файл после кэширования |
| `CAPTION_FILE` | `/app/caption.txt` | Файл с постоянной подписью под видео (читается при каждой отправке) |
| `BROADCAST_TIMEOUT_SECONDS` | `300` | Авто-выключение режима рассылки без активности |
| `EMBED_SUBTITLES` | `false` | Вжигать субтитры (ru/en) в кадр видео |

---

## 🧪 Тесты

```bash
# Установить зависимости для разработки
pip install -r requirements-dev.txt

# Запустить все тесты с отчётом покрытия
pytest tests/ --cov=app --cov-report=term-missing -v

# Только быстрая проверка
pytest tests/ -q
```

**Результат:**

```
676 tests
```

Тесты покрывают все модули изолированно через моки — без реального Docker, Redis, PostgreSQL, Telegram API или запуска настоящего Chromium. Включают:
- Unit-тесты для всех утилит, сервисов, моделей (`platforms`, `broadcast`, `caption`, `codecs`, `rezka`, `proxy_check`, `proxy_format`)
- Тесты AccessMiddleware, AdminFilter и всех sub-роутеров: доступ, cookie-загрузка, режим рассылки, Google OAuth, поток выбора серий rezka
- Тесты Celery-задачи: прогресс-хук, кэш `file_id`, дедупликация, прокси/VPN-маршрутизация rezka, материализация персональных cookie
- Тесты `downloader.py`: выбор формата/кодека, ретраи, транскодирование, компрессия под лимит
- Тесты системы логирования с исправлением Windows file-lock

---

## 📁 Структура проекта

```
Video_down/
│
├── app/
│   ├── bot/
│   │   ├── main.py              # Запуск в режиме webhook или polling, регистрация команд
│   │   ├── router.py            # Агрегатор sub-роутеров; подключает AccessMiddleware
│   │   ├── access.py            # _is_admin, _check_access — логика доступа
│   │   ├── middleware.py        # AccessMiddleware — блокирует неавторизованных до хэндлера
│   │   ├── filters.py           # AdminFilter — aiogram-фильтр для команд администратора
│   │   ├── utils.py             # safe_edit_text — edit_text без TelegramBadRequest
│   │   └── routers/
│   │       ├── user.py          # /start, /help, /quality, /status
│   │       ├── admin.py         # /admin, /adduser, /removeuser, /listusers, лимиты, /addproxy, /delproxy, /listproxies
│   │       ├── broadcast.py     # /broadcast, BroadcastModeFilter, рассылка всем
│   │       ├── cookies.py       # /cookies, /delcookies, загрузка файлов cookie
│   │       ├── oauth.py         # /link_google, /unlink_google, Google OAuth2 device flow
│   │       ├── rezka_flow.py    # Inline-выбор озвучки/сезона/серии rezka, навигация назад
│   │       └── url_handler.py   # Обработка ссылок: rate limit, кэш, очередь
│   ├── core/
│   │   ├── config.py            # Pydantic Settings — типизированный .env с кэшем
│   │   └── logging.py           # Ротирующие файловые логи + вывод в stdout
│   ├── db/
│   │   ├── models.py            # SQLAlchemy модели: User, Video, DownloadRequest, UserCookies, UserGoogleToken, Proxy
│   │   ├── repository.py        # ISP-репозитории: UserRepository, CookieRepository, VideoRepository, RequestRepository, ProxyRepository, GoogleTokenRepository
│   │   ├── session.py           # Фабрика сессий БД
│   │   └── utils.py             # Вспомогательные функции БД
│   ├── keyboards/
│   │   ├── admin.py             # Инлайн-клавиатура панели администратора, лимитов и выбора схемы прокси
│   │   ├── quality.py           # Инлайн-клавиатура выбора качества
│   │   └── rezka.py             # Инлайн-клавиатуры выбора озвучки/сезона/серии rezka
│   ├── services/
│   │   ├── rate_limiter.py      # Redis rate limiter с атомарными Lua-скриптами + check_rate_limit()
│   │   ├── redis_client.py      # Синглтон-клиент Redis
│   │   ├── runtime_config.py    # Динамические лимиты через Redis (get_limit, get_effective_limits)
│   │   ├── google_oauth.py      # Google OAuth2 device flow — токены и генерация YouTube cookies
│   │   ├── rezka_session.py     # Redis-сессия пошагового выбора озвучки/сезона/серии rezka
│   │   ├── proxy_awaiting.py    # Состояние диалога /addproxy (ожидание ввода прокси)
│   │   └── cleanup.py           # Часовая автоочистка downloads/active/ (Celery Beat)
│   ├── utils/
│   │   ├── quality.py           # Нормализация и форматирование качества для yt-dlp
│   │   ├── codecs.py            # Фильтры кодеков (H.264 → HEVC → любой; избегание VP9/AV1)
│   │   ├── url_tools.py         # Извлечение URL, валидация, нормализация, SHA256-хэш
│   │   ├── platforms.py         # Определение платформы по URL и cookie-файлу
│   │   ├── caption.py           # Чтение постоянной подписи из caption.txt
│   │   ├── broadcast.py         # Парсер inline-кнопок для рассылки
│   │   ├── rezka.py             # Резолв потока rezka: антибот-обход, зеркала, озвучки/серии, сессии
│   │   ├── proxy_check.py       # Живая проверка прокси на YouTube перед сохранением
│   │   ├── proxy_format.py      # Парсинг разных форматов ввода прокси (IP:PORT[:LOGIN:PASS] и т.д.)
│   │   └── telegram_session.py  # aiohttp-сессия для Local Bot API
│   └── worker/
│       ├── celery_app.py        # Конфигурация Celery (очередь downloads, beat-расписание очистки)
│       ├── downloader.py        # Обёртка над yt-dlp (опции, кодеки, транскодирование, cookies)
│       ├── tasks.py             # Celery-задача + хелперы: кэш, прогресс-хук, прокси/VPN-маршрутизация, rezka
│       └── telegram_sender.py   # Синхронный мост для вызова asyncio из Celery-воркера
│
├── alembic/
│   └── versions/
│       ├── 0001_initial.py            # Начальная миграция (users, videos, download_requests)
│       ├── 0002_user_ban_columns.py   # Идемпотентно добавляет is_banned / banned_until
│       ├── 0003_user_cookies.py       # Таблица user_cookies (персональные cookie)
│       ├── 0004_google_oauth_token.py # Таблица user_google_tokens (refresh-токены Google)
│       └── 0005_proxies.py            # Таблица proxies (пул для /addproxy)
│
├── nginx/
│   └── video-bot.conf           # HTTPS, rate limit, проксирование на бот:8080
│
├── vpn/
│   ├── README.md                 # Как получить .ovpn и настроить VPN-фоллбек для rezka
│   └── vpn.ovpn                  # Конфиг OpenVPN-провайдера (не в git)
│
├── scripts/
│   ├── setup.sh                 # Скрипт автоустановки (Docker, SSL, systemd, .env)
│   ├── renew_cert.sh             # Автоперевыпуск Let's Encrypt (генерируется setup.sh)
│   ├── cleanup_downloads.py      # Ручной запуск очистки downloads/active/
│   └── update_ytdlp.sh           # Обновление yt-dlp без пересборки образа
│
├── tests/                       # 676 тестов
│   ├── test_bot_router.py               # Тесты всех sub-роутеров, AccessMiddleware, AdminFilter
│   ├── test_bot_routers_rezka_flow.py   # Тесты inline-выбора озвучки/сезона/серии
│   ├── test_worker_tasks.py             # Тесты Celery-задачи и вспомогательных функций
│   ├── test_worker_downloader.py        # Тесты выбора формата, кодеков, транскодирования
│   ├── test_utils_rezka.py              # Тесты резолва rezka: зеркала, антибот, сессии
│   └── ...                              # Тесты каждого модуля
│
├── downloads/                   # Временные файлы (монтируется в контейнер)
├── logs/                        # Логи приложения и certbot
├── cookies/                     # Глобальные cookie-файлы для yt-dlp (не в git)
├── caption.txt                  # Постоянная подпись под видео (правится на лету)
├── Dockerfile                   # Образ бота/воркера + ffmpeg + Deno + Playwright Chromium
├── docker-compose.yml           # postgres, redis, bot, worker + опциональные профили: webhook (nginx), local-bot-api, rezka-vpn
├── .env.example                 # Шаблон переменных окружения
└── requirements.txt             # aiogram, Celery, SQLAlchemy, yt-dlp, HdRezkaApi, Playwright, ...
```

---

## 🏭 Рекомендации для продакшена

### Небольшой VPS (1–2 CPU, 2 GB RAM)

```env
USER_QUEUE_LIMIT=2
GLOBAL_QUEUE_LIMIT=10
MAX_ACTIVE_DOWNLOADS_PER_USER=1
RATE_LIMIT_MAX_MESSAGES=5
```

Держите `REZKA_ANTIBOT_BYPASS` выключенным, если CPU и так впритык — headless Chromium под rezka заметно тяжелее обычной загрузки.

### Крупный сервер — масштабирование воркеров

```bash
# Запустить 3 параллельных воркера
docker compose up -d --scale worker=3
```

При масштабировании увеличьте:
```env
GLOBAL_QUEUE_LIMIT=100
USER_QUEUE_LIMIT=5
```

### Обновление yt-dlp без пересборки образа

```bash
bash scripts/update_ytdlp.sh
```

### Мониторинг очереди Celery

```bash
docker compose exec worker celery -A app.worker.celery_app:celery_app inspect active
docker compose exec worker celery -A app.worker.celery_app:celery_app inspect stats
```

---

## 📄 Лицензия

MIT — используйте свободно.
