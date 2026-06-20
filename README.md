<div align="center">

# 🎬 Video Downloader Bot

**Telegram-бот для скачивания публичных видео по ссылке**

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-blue?logo=telegram)](https://docs.aiogram.dev)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://docs.docker.com/compose/)
[![Tests](https://img.shields.io/badge/Tests-342%20passed-brightgreen?logo=pytest)](tests/)
[![Coverage](https://img.shields.io/badge/Coverage-high-brightgreen)](tests/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

Отправь боту ссылку — получи видео прямо в чат.  
Поддерживает YouTube, VK, TikTok, Instagram, Facebook и [1000+ других сайтов](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

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
- [Cookie-файлы](#-cookie-файлы)
- [Переменные окружения](#-переменные-окружения)
- [Тесты](#-тесты)
- [Структура проекта](#-структура-проекта)
- [Рекомендации для продакшена](#-рекомендации-для-продакшена)

---

## ✨ Ключевые возможности

### 📥 Загрузка видео
- **1000+ сайтов** — YouTube, VK, TikTok, Instagram, Facebook, Twitter/X, Twitch, Dailymotion и другие через yt-dlp
- **Выбор качества** — 360p / 480p / 720p / 1080p / наилучшее / только аудио
- **Кэш `file_id`** — если видео уже скачивалось, бот мгновенно отправляет его повторно из кэша Telegram без повторной загрузки
- **Автоматические повторные попытки** — yt-dlp: 3 попытки на запрос, 5 на фрагмент
- **JS-рантайм Deno** в образе — нужен yt-dlp для обхода плеер-челленджей YouTube
- **Постоянная подпись** под каждым видео — берётся из файла `caption.txt`, меняется на лету без пересборки
- **Персональные cookie** для каждого пользователя — загружаются файлом прямо боту и хранятся в PostgreSQL (см. [Cookie-файлы](#-cookie-файлы))

### 📢 Рассылка администратора
- Режим рассылки: всё, что админ отправит в чат (текст, фото, GIF, видео, музыка, эмодзи), уходит всем пользователям через `copy_message`
- **Inline-кнопки** в рассылке — синтаксис `Текст | https://ссылка` после строки `---`
- **Таймер-защита** — режим авто-выключается через 5 минут без активности; каждая рассылка сбрасывает таймер; есть inline-кнопка отмены
- Сохраняются **все пользователи**, кто хоть раз запускал бота — они и получают рассылку

### 🔐 Управление доступом
- **Три режима**: публичный бот / статичный список в `.env` / динамический список доверенных через `/adduser`
- **Панель администратора** — инлайн-меню с кнопкой мгновенного включения/выключения бота для всех
- **Администратор всегда имеет доступ** — глобальное отключение бота на него не распространяется

### 🛡️ Антиспам и защита
- **Rate limiter** — скользящее временное окно с автоматическим баном нарушителей
- **Атомарные Lua-скрипты** в Redis — счётчики без race condition при параллельных запросах
- **Дневной лимит** запросов на пользователя
- **Лимит активных задач** на пользователя и глобально
- **Дедупликация** — одно и то же видео с одним качеством не скачивается дважды параллельно (блокировка по `url_hash + quality`)
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
- **Прогресс-хук** — пользователь видит обновления статуса в реальном времени во время загрузки
- **Alembic-миграции** применяются автоматически при каждом запуске бота
- **Repository паттерн** — единая точка доступа к данным, изолированная от бизнес-логики
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
| Очередь задач | [Celery 5](https://docs.celeryq.dev) + Redis — асинхронное скачивание |
| База данных | PostgreSQL 16 + [SQLAlchemy 2.0](https://docs.sqlalchemy.org) ORM |
| Миграции БД | [Alembic](https://alembic.sqlalchemy.org) — автоприменение при старте |
| Скачивание видео | [yt-dlp](https://github.com/yt-dlp/yt-dlp) + ffmpeg — 1000+ сайтов |
| Кэш / блокировки | [Redis](https://redis.io) — rate limiting, file_id кэш, дедупликация |
| Конфигурация | [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — типизированный `.env` |
| Обратный прокси | Nginx 1.27 — HTTPS терминация, rate limit |
| SSL | [Let's Encrypt](https://letsencrypt.org) + certbot — выпуск и автоперевыпуск |
| Контейнеры | Docker Compose — 4 сервиса с healthcheck |
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
| *(любая ссылка)* | Скачать видео. Ссылку можно прислать отдельным сообщением или вставить в подпись к медиа |
| *(файл `<platform>.txt`)* | Загрузить личные cookie (имя файла задаёт платформу) |

### Команды администратора

| Команда | Описание |
|---|---|
| `/admin` | Панель администратора: статус бота, режим доступа, кнопки вкл/выкл и рассылки |
| `/broadcast` | Включить режим рассылки всем пользователям |
| `/adduser <id>` | Добавить пользователя в доверенные по Telegram ID |
| `/removeuser <id>` | Убрать пользователя из доверенных |
| `/listusers` | Список всех доверенных пользователей |

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

Переключить кнопкой "🔴 Выключить бот для всех" / "🟢 Включить бот для всех" в панели `/admin`.

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

## 🍪 Cookie-файлы

Некоторые сайты требуют cookie авторизованного аккаунта (приватные/возрастные видео, а YouTube на VPS почти всегда — иначе ошибка «Sign in to confirm you're not a bot»). Поддерживаются два уровня cookie.

### Персональные cookie (рекомендуется)

Каждый пользователь загружает свои cookie прямо боту — они хранятся в PostgreSQL (таблица `user_cookies`) и доступны воркеру без общих файловых маунтов. При скачивании воркер материализует временный cookie-файл и удаляет его после.

1. Экспортируйте cookie в формате **Netscape** (`cookies.txt`) из браузера, где вы вошли в аккаунт (расширение [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/), или `yt-dlp --cookies-from-browser chrome --cookies youtube.txt`).
2. Переименуйте файл по платформе: `youtube.txt`, `instagram.txt`, `tiktok.txt` или `facebook.txt`.
3. Просто пришлите файл боту. Проверить статус — `/cookies`, удалить — `/delcookies youtube`.

Если YouTube вернёт ошибку про cookie, бот сам подскажет в чате, что нужно прислать `youtube.txt`.

### Глобальные cookie (fallback)

Можно положить общие cookie-файлы в папку `cookies/` — они используются, если у пользователя нет личных:

```
cookies/
├── youtube.txt
├── facebook.txt
├── instagram.txt
└── tiktok.txt
```

**Приоритет:** личные cookie пользователя → глобальный файл платформы → без cookie.

**Отключить cookies полностью:**
```env
USE_COOKIES=false
```

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

### Загрузка

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DEFAULT_QUALITY` | `720p` | Качество по умолчанию |
| `MAX_FILE_MB` | `50` | Макс. размер файла (лимит Telegram Bot API — 50 МБ) |
| `DOWNLOAD_TIMEOUT_SECONDS` | `900` | Таймаут скачивания (15 минут) |
| `MAX_ACTIVE_DOWNLOADS_PER_USER` | `1` | Макс. параллельных загрузок на пользователя |
| `MAX_DOWNLOAD_DURATION_SECONDS` | `1800` | Макс. длительность видео (30 минут) |

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
342 passed
```

Тесты покрывают все модули изолированно через моки — без реального Docker, Redis или PostgreSQL. Включают:
- Unit-тесты для всех утилит, сервисов, моделей (включая `platforms`, `broadcast`, `caption`)
- Тесты всех веток обработчиков роутера: доступ, cookie-загрузка, режим рассылки
- Тесты Celery-задачи, включая прогресс-хук и материализацию персональных cookie
- Тесты системы логирования с исправлением Windows file-lock

---

## 📁 Структура проекта

```
Video_down/
│
├── app/
│   ├── bot/
│   │   ├── main.py              # Запуск в режиме webhook или polling, регистрация команд
│   │   └── router.py            # Обработчики: доступ, антиспам, скачивание, /admin, cookie, рассылка
│   ├── core/
│   │   ├── config.py            # Pydantic Settings — типизированный .env с кэшем
│   │   └── logging.py           # Ротирующие файловые логи + вывод в stdout
│   ├── db/
│   │   ├── models.py            # SQLAlchemy модели: User, Video, DownloadRequest, UserCookies
│   │   ├── repository.py        # Repository паттерн — User/Cookie/Video/Request репозитории
│   │   ├── session.py           # Фабрика сессий БД
│   │   └── utils.py             # Вспомогательные функции БД
│   ├── keyboards/
│   │   ├── admin.py             # Инлайн-клавиатура панели администратора
│   │   └── quality.py           # Инлайн-клавиатура выбора качества
│   ├── services/
│   │   ├── rate_limiter.py      # Redis rate limiter с атомарными Lua-скриптами
│   │   └── redis_client.py      # Синглтон-клиент Redis
│   ├── utils/
│   │   ├── quality.py           # Нормализация и форматирование качества для yt-dlp
│   │   ├── url_tools.py         # Извлечение URL, валидация, нормализация, SHA256-хэш
│   │   ├── platforms.py         # Определение платформы по URL и cookie-файлу
│   │   ├── caption.py           # Чтение постоянной подписи из caption.txt
│   │   └── broadcast.py         # Парсер inline-кнопок для рассылки
│   └── worker/
│       ├── celery_app.py        # Конфигурация Celery (очередь downloads)
│       ├── downloader.py        # Обёртка над yt-dlp (опции, выбор файла, личные/глобальные cookies)
│       ├── tasks.py             # Celery-задача: скачать → отправить → закэшировать
│       └── telegram_sender.py   # Синхронный мост для вызова asyncio из Celery-воркера
│
├── alembic/
│   └── versions/
│       ├── 0001_initial.py      # Начальная миграция (users, videos, download_requests)
│       ├── 0002_user_ban_columns.py   # Идемпотентно добавляет is_banned / banned_until
│       └── 0003_user_cookies.py       # Таблица user_cookies (персональные cookie)
│
├── nginx/
│   └── video-bot.conf           # HTTPS, rate limit, проксирование на бот:8080
│
├── scripts/
│   ├── setup.sh                 # Скрипт автоустановки (Docker, SSL, systemd, .env)
│   ├── renew_cert.sh            # Автоперевыпуск Let's Encrypt (генерируется setup.sh)
│   └── update_ytdlp.sh          # Обновление yt-dlp без пересборки образа
│
├── tests/                       # 342 теста
│   ├── test_bot_router.py       # Тесты всех обработчиков, доступа, cookie, рассылки
│   ├── test_worker_tasks.py     # Тесты Celery-задачи
│   └── ...                      # Тесты каждого модуля
│
├── downloads/                   # Временные файлы (монтируется в контейнер)
├── logs/                        # Логи приложения и certbot
├── cookies/                     # Глобальные cookie-файлы для yt-dlp (не в git)
├── caption.txt                  # Постоянная подпись под видео (правится на лету)
├── Dockerfile                   # Образ бота/воркера + ffmpeg + Deno
├── docker-compose.yml
├── .env.example                 # Шаблон переменных окружения
└── requirements.txt             # aiogram, Celery, SQLAlchemy, yt-dlp, ...
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
