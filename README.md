# 🎬 Video Downloader Bot

Telegram-бот для скачивания публичных видео по ссылке и отправки файла прямо в чат.  
Построен на **aiogram 3**, работает через **webhook**, задачи обрабатывает **Celery**-воркер.

---

## Стек технологий

| Компонент | Технология |
|---|---|
| Telegram Bot | aiogram 3.x + aiohttp webhook |
| Очередь задач | Celery 5 + Redis |
| База данных | PostgreSQL 16 + SQLAlchemy 2.0 ORM |
| Миграции | Alembic |
| Скачивание | yt-dlp |
| Кэш / блокировки | Redis |
| Обратный прокси | Nginx (HTTPS, rate-limit) |
| Контейнеры | Docker Compose |
| Системная служба | systemd (`video-bot.service`) |

---

## Возможности

### Загрузка видео
- Скачивает видео по ссылке (YouTube, VK, TikTok, Instagram, Facebook и любые другие сайты, поддерживаемые yt-dlp)
- Выбор качества: 360p / 480p / 720p / 1080p / лучшее
- Кэш `file_id` — если видео уже скачивалось, бот мгновенно отправляет его из Telegram без повторной загрузки
- Поддержка cookie-файлов для авторизованного доступа к Facebook, Instagram, TikTok

### Управление доступом (три режима)
| Режим | Условие | Поведение |
|---|---|---|
| **Публичный** | `ALLOWED_USERS` пуст и нет доверенных | Доступен всем |
| **Статичный список** | `ALLOWED_USERS` заполнен в `.env` | Только перечисленные ID |
| **Доверенные пользователи** | Добавлены через `/adduser` | Только добавленные администратором |

Администратор всегда имеет доступ вне зависимости от режима.  
Команда `/admin` позволяет включить/выключить бот для всех одной кнопкой.

### Антиспам и лимиты
- Rate limiter: скользящее окно + временный бан
- Дневной лимит запросов на пользователя
- Лимит активных задач на пользователя
- Глобальный лимит очереди
- Атомарные Lua-скрипты в Redis для счётчиков без race condition
- Nginx rate limit на webhook-эндпоинт

### Безопасность
- Нет сырого SQL — весь доступ через SQLAlchemy ORM
- Webhook защищён `X-Telegram-Bot-Api-Secret-Token`
- Duplicate lock по `url_hash + quality` — одно и то же видео не скачивается параллельно дважды
- SSL/TLS через Let's Encrypt с автоперевыпуском

---

## Быстрый старт (автоустановка)

Самый простой способ — запустить интерактивный скрипт настройки на чистом Ubuntu-сервере:

```bash
git clone https://github.com/Poleno7682/Video_down.git
cd Video_down
sudo bash scripts/setup.sh
```

Скрипт пошагово:
1. Проверяет и устанавливает Docker Engine (официальный репозиторий), certbot, curl и остальные зависимости
2. Спрашивает токен бота, данные PostgreSQL, домен, email и Telegram ID администратора
3. Выпускает SSL-сертификат Let's Encrypt (`--standalone`)
4. Настраивает автоперевыпуск сертификата через cron (ежедневно в 03:15)
5. Генерирует `nginx/video-bot.conf` и `.env`
6. Создаёт и включает системную службу `video-bot.service` (systemd)
7. Собирает Docker-образы и запускает весь стек
8. Проверяет доступность через health-check

После завершения бот доступен по адресу `https://ваш-домен/telegram/webhook`.

---

## Ручная установка

### 1. Требования

- Ubuntu 22.04+ (или Debian 12+)
- Docker Engine с docker-compose-plugin
- Домен с A-записью, указывающей на сервер (для SSL)

### 2. Клонировать и настроить окружение

```bash
git clone https://github.com/Poleno7682/Video_down.git
cd Video_down
cp .env.example .env
nano .env
```

Обязательные параметры:

```env
BOT_TOKEN=123456789:ваш_токен_от_BotFather
WEBHOOK_BASE_URL=https://ваш-домен.com
WEBHOOK_SECRET=длинный_случайный_секрет
POSTGRES_PASSWORD=надёжный_пароль
DATABASE_URL=postgresql+psycopg2://video_bot:надёжный_пароль@postgres:5432/video_bot
ADMIN_USERS=ваш_telegram_id
```

### 3. SSL-сертификат

```bash
# Остановите nginx если запущен
sudo certbot certonly --standalone -d ваш-домен.com --email ваш@email.com --agree-tos

# Скопируйте сертификаты
mkdir -p nginx/certs
sudo cp /etc/letsencrypt/live/ваш-домен.com/fullchain.pem nginx/certs/
sudo cp /etc/letsencrypt/live/ваш-домен.com/privkey.pem   nginx/certs/
chmod 644 nginx/certs/*.pem
```

### 4. Nginx-конфиг

Замените `your-domain.com` в `nginx/video-bot.conf` на ваш реальный домен:

```bash
sed -i 's/your-domain.com/ваш-домен.com/g' nginx/video-bot.conf
```

### 5. Запуск

```bash
docker compose up -d --build
```

### 6. Проверка

```bash
docker compose ps
docker compose logs -f bot
curl https://ваш-домен.com/health
```

---

## Управление службой

После автоустановки проект зарегистрирован как systemd-служба `video-bot`:

```bash
systemctl status video-bot      # статус
systemctl restart video-bot     # перезапуск всего стека
systemctl stop video-bot        # остановка
systemctl start video-bot       # запуск
journalctl -u video-bot -f      # логи запуска/остановки
```

Управление отдельными контейнерами:

```bash
docker compose logs -f bot      # логи бота
docker compose logs -f worker   # логи воркера
docker compose ps               # статус контейнеров
```

---

## Команды бота

### Пользовательские команды

| Команда | Описание |
|---|---|
| `/start`, `/help` | Справка по боту |
| `/quality` | Выбрать качество видео (360p–1080p / best) |
| `/status` | Статус очереди: активные задачи, дневные запросы |
| *(любая ссылка)* | Скачать видео по URL |

### Команды администратора

| Команда | Описание |
|---|---|
| `/admin` | Панель администратора — статус бота, режим доступа, кнопка вкл/выкл |
| `/adduser <id>` | Добавить пользователя в доверенные (по Telegram ID) |
| `/removeuser <id>` | Убрать пользователя из доверенных |
| `/listusers` | Список всех доверенных пользователей |

Кнопка в `/admin` позволяет мгновенно отключить бот для всех пользователей (кроме администраторов) — полезно при обслуживании сервера.

---

## Cookie-файлы

Для скачивания видео с Facebook, Instagram, TikTok (приватный или возрастной контент) положите cookie-файлы в формате Netscape в папку `cookies/`:

```
cookies/facebook.txt
cookies/instagram.txt
cookies/tiktok.txt
```

Экспортировать cookies можно браузерным расширением [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/).

При `USE_COOKIES=false` в `.env` cookies не используются.

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен бота от @BotFather |
| `WEBHOOK_BASE_URL` | — | Публичный URL сервера (`https://example.com`) |
| `WEBHOOK_PATH` | `/telegram/webhook` | Путь вебхука |
| `WEBHOOK_SECRET` | — | Секрет для проверки запросов от Telegram |
| `POSTGRES_PASSWORD` | — | Пароль PostgreSQL |
| `DATABASE_URL` | — | Полная строка подключения к БД |
| `REDIS_URL` | `redis://redis:6379/0` | URL Redis |
| `ADMIN_USERS` | — | Telegram ID администраторов через запятую |
| `ALLOWED_USERS` | — | Статичный белый список ID (пусто = публичный бот) |
| `DEFAULT_QUALITY` | `720p` | Качество по умолчанию |
| `MAX_FILE_MB` | `50` | Максимальный размер файла для отправки |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Окно антиспама (сек) |
| `RATE_LIMIT_MAX_MESSAGES` | `8` | Макс. запросов в окне |
| `BAN_SECONDS` | `600` | Длительность бана за спам |
| `USER_DAILY_LIMIT` | `50` | Дневной лимит запросов на пользователя |
| `USER_QUEUE_LIMIT` | `3` | Макс. активных задач на пользователя |
| `GLOBAL_QUEUE_LIMIT` | `50` | Глобальный лимит очереди |
| `CACHE_TTL_HOURS` | `168` | Срок хранения кэша `file_id` (7 дней) |
| `USE_COOKIES` | `true` | Использовать cookie-файлы |
| `DOWNLOAD_TIMEOUT_SECONDS` | `900` | Таймаут скачивания |

---

## Тесты

```bash
pip install -r requirements-dev.txt
pytest tests/ --cov=app --cov-report=term-missing
```

Покрытие: **100%** (893 инструкции, 289 тестов).

---

## Рекомендации для продакшена

**Небольшой VPS (1–2 CPU, 2 GB RAM):**
```env
USER_QUEUE_LIMIT=2
GLOBAL_QUEUE_LIMIT=10
MAX_ACTIVE_DOWNLOADS_PER_USER=1
```

**Крупный сервер — несколько воркеров:**
```bash
docker compose up -d --scale worker=3
```

При масштабировании воркеров увеличьте `GLOBAL_QUEUE_LIMIT` и выделите больше ресурсов PostgreSQL/Redis.

---

## Структура проекта

```
.
├── app/
│   ├── bot/
│   │   ├── main.py          # Запуск webhook-сервера, systemd-команды
│   │   └── router.py        # Все обработчики, доступ, антиспам
│   ├── core/
│   │   ├── config.py        # Pydantic Settings
│   │   └── logging.py       # Ротирующие логи
│   ├── db/
│   │   ├── models.py        # SQLAlchemy модели
│   │   ├── repository.py    # Repository паттерн
│   │   └── session.py       # Сессии БД
│   ├── keyboards/
│   │   ├── admin.py         # Инлайн-клавиатура администратора
│   │   └── quality.py       # Инлайн-клавиатура качества
│   ├── services/
│   │   ├── rate_limiter.py  # Антиспам через Redis Lua
│   │   └── redis_client.py  # Клиент Redis
│   ├── utils/
│   │   ├── quality.py       # Нормализация качества
│   │   └── url_tools.py     # Извлечение и хэширование URL
│   └── worker/
│       ├── celery_app.py    # Конфигурация Celery
│       ├── downloader.py    # Скачивание через yt-dlp
│       ├── tasks.py         # Celery-задача
│       └── telegram_sender.py # Отправка файлов в Telegram
├── alembic/                 # Миграции БД
├── nginx/
│   └── video-bot.conf       # Конфиг Nginx (HTTPS + rate limit)
├── scripts/
│   └── setup.sh             # Скрипт автоустановки
├── tests/                   # 289 тестов, покрытие 100%
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

---

## Лицензия

MIT
