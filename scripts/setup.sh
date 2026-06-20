#!/usr/bin/env bash
# =============================================================================
# setup.sh — Первоначальная настройка Video Bot
# Запускать от root: sudo bash scripts/setup.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# ─── Цвета и UI ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

step()  { echo -e "\n${BLUE}${BOLD}▶  $*${NC}"; }
ok()    { echo -e "   ${GREEN}✓  $*${NC}"; }
warn()  { echo -e "   ${YELLOW}⚠  $*${NC}"; }
die()   { echo -e "\n${RED}${BOLD}✗  $*${NC}" >&2; exit 1; }
info()  { echo -e "   ${BOLD}$*${NC}"; }

# Прогресс-бар: progress <процент 0-100> <сообщение>
progress() {
    local pct="$1" msg="${2:-}" w=40 filled=0 bar=""
    filled=$(( pct * w / 100 ))
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=filled; i<w; i++)); do bar+="░"; done
    echo -e "\n   ${BLUE}▐${GREEN}${bar}${BLUE}▌${NC} ${BOLD}${GREEN}${pct}%${NC}  ${msg}"
}

# Читает непустую строку. Необязательное значение по умолчанию — третий аргумент.
ask() {
    local prompt="$1" var_name="$2" default="${3:-}"
    local display="$prompt"
    [[ -n "$default" ]] && display="$prompt [${default}]"
    while true; do
        printf "   %b%s: %b" "${BOLD}" "$display" "${NC}"
        read -r val
        val="${val:-$default}"
        if [[ -n "$val" ]]; then
            printf -v "$var_name" '%s' "$val"
            return
        fi
        echo -e "   ${RED}Поле обязательно для заполнения.${NC}"
    done
}

# Читает без отображения (пароль/секрет).
ask_secret() {
    local prompt="$1" var_name="$2"
    while true; do
        printf "   %b%s: %b" "${BOLD}" "$prompt" "${NC}"
        read -rs val
        echo
        if [[ -n "$val" ]]; then
            printf -v "$var_name" '%s' "$val"
            return
        fi
        echo -e "   ${RED}Поле обязательно для заполнения.${NC}"
    done
}

ask_yn() {
    local prompt="$1" default="${2:-y}"
    local display="$prompt [Y/n]"
    [[ "$default" == "n" ]] && display="$prompt [y/N]"
    printf "   %b%s: %b" "${BOLD}" "$display" "${NC}"
    read -r val
    val="${val:-$default}"
    [[ "${val,,}" == "y" || "${val,,}" == "yes" ]]
}

gen_secret() { openssl rand -hex 32; }

# Возвращает 0, если бинарник доступен в PATH.
have() { command -v "$1" &>/dev/null; }

# ─── Проверки ─────────────────────────────────────────────────────────────────
preflight() {
    [[ $EUID -eq 0 ]] || die "Запустите от root: sudo bash scripts/setup.sh"
    [[ -f "$PROJECT_DIR/docker-compose.yml" ]] || \
        die "Не найден docker-compose.yml. Запускайте из корня проекта."

    # apt-get должен быть доступен (Ubuntu / Debian)
    have apt-get || die "apt-get не найден. Скрипт рассчитан на Ubuntu/Debian."

    progress 3 "Предварительные проверки пройдены"
}

# ─── Установка Docker Engine через официальный репозиторий ───────────────────
_install_docker_official() {
    info "Docker не найден — устанавливаю Docker Engine (официальный репозиторий)…"
    warn "Это займёт 2–4 минуты…"

    progress 8 "Подготовка репозитория Docker…"
    apt-get update -qq
    apt-get install -y --no-install-recommends ca-certificates curl gnupg >/dev/null

    progress 13 "Добавление GPG-ключа Docker Inc…"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    progress 17 "Подключение официального репозитория…"
    local arch codename
    arch="$(dpkg --print-architecture)"
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${codename} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq

    progress 22 "Загрузка и установка пакетов Docker (может занять 2–3 мин.)…"
    apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin \
        >/dev/null

    progress 32 "Docker Engine установлен"
    ok "$(docker --version)"
}

# ─── Шаг 1: Проверка и установка зависимостей ────────────────────────────────
install_deps() {
    step "Проверка и установка зависимостей"

    # ── Python-пакеты (requirements.txt) ──────────────────────────────────────
    [[ -f "$PROJECT_DIR/requirements.txt" ]] \
        || die "requirements.txt не найден в $PROJECT_DIR — установка невозможна."
    local req_count
    req_count=$(grep -vc '^\s*$' "$PROJECT_DIR/requirements.txt")
    ok "requirements.txt найден (${req_count} пакетов — устанавливаются в Docker-образ при сборке)"
    progress 5 "requirements.txt проверен"

    # ── Docker Engine ──────────────────────────────────────────────────────────
    if have docker; then
        ok "docker — уже установлен: $(docker --version)"
        progress 32 "Docker уже установлен"
    else
        _install_docker_official
    fi

    # docker compose v2
    if docker compose version &>/dev/null; then
        ok "docker compose v2 — уже установлен"
    else
        warn "docker-compose-plugin — не найден, устанавливаю…"
        apt-get install -y --no-install-recommends docker-compose-plugin >/dev/null
        ok "docker-compose-plugin установлен"
    fi
    progress 35 "Docker Compose v2 готов"

    # ── certbot через snap (изолированные зависимости, без конфликтов urllib3) ──
    _install_certbot_snap() {
        info "Устанавливаю certbot через snap…"
        if ! have snap; then
            apt-get install -y --no-install-recommends snapd >/dev/null
            systemctl enable --now snapd.socket snapd 2>/dev/null || true
            sleep 2
        fi
        snap install --classic certbot
        ln -sf /snap/bin/certbot /usr/bin/certbot
        ok "certbot установлен через snap: $(certbot --version 2>&1)"
    }

    if have certbot; then
        # Проверяем, что он реально работает (apt-версия может давать ImportError)
        if certbot --version &>/dev/null 2>&1; then
            ok "certbot — уже установлен и работает: $(certbot --version 2>&1)"
        else
            warn "certbot найден, но даёт ошибку (конфликт urllib3) — переустанавливаю через snap"
            apt-get remove -y certbot python3-certbot 2>/dev/null || true
            _install_certbot_snap
        fi
    else
        _install_certbot_snap
    fi

    # ── Остальные системные пакеты ─────────────────────────────────────────────
    declare -A PKG_MAP=(
        [openssl]="openssl"
        [curl]="curl"
        [lsof]="lsof"
        [python3]="python3"
    )

    local missing_pkgs=()
    for bin in openssl curl lsof python3; do
        if have "$bin"; then
            ok "${bin} — уже установлен ($(command -v "$bin"))"
        else
            warn "${bin} — не найден, будет установлен"
            missing_pkgs+=("${PKG_MAP[$bin]}")
        fi
    done

    if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
        info "Устанавливаю недостающие пакеты: ${missing_pkgs[*]}"
        apt-get update -qq
        apt-get install -y --no-install-recommends "${missing_pkgs[@]}" >/dev/null
        ok "Установлено: ${missing_pkgs[*]}"
    else
        ok "Все остальные зависимости уже установлены — apt-get пропущен"
    fi
    progress 39 "Системные зависимости установлены"

    # ── Docker daemon ──────────────────────────────────────────────────────────
    if systemctl is-active --quiet docker; then
        ok "Docker daemon уже запущен"
    else
        systemctl enable --now docker
        ok "Docker daemon запущен и добавлен в автозапуск"
    fi

    # Финальные проверки
    docker compose version &>/dev/null \
        || die "docker compose (v2) не работает. Проверьте: apt-get install docker-compose-plugin"
    docker info &>/dev/null \
        || die "Docker daemon недоступен. Проверьте: systemctl status docker"

    progress 42 "Все зависимости готовы"
}

# ─── Шаг 2: Сбор конфигурации ────────────────────────────────────────────────
collect_config() {
    progress 44 "Сбор конфигурации…"
    echo ""
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${BLUE}   Настройка Video Bot — ответьте на несколько вопросов${NC}"
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    step "Telegram Bot"
    info "Создайте бота через @BotFather и скопируйте токен."
    ask "Токен бота (BOT_TOKEN)" BOT_TOKEN

    step "PostgreSQL"
    ask "Хост PostgreSQL (внутри Docker: 'postgres')" PG_HOST "postgres"
    ask "Название базы данных" PG_DB "video_bot"
    ask "Пользователь БД" PG_USER "video_bot"
    ask_secret "Пароль БД" PG_PASSWORD

    step "Домен и SSL"
    info "Домен должен уже указывать A-записью на IP этого сервера."
    ask "Домен (например: bot.example.com)" DOMAIN
    ask "Email для Let's Encrypt (уведомления об истечении сертификата)" LE_EMAIL

    step "Администратор"
    info "Откройте @userinfobot в Telegram, чтобы узнать свой числовой ID."
    ask "Telegram ID администратора" ADMIN_ID

    WEBHOOK_SECRET="$(gen_secret)"
    ok "Webhook secret сгенерирован"
    progress 50 "Конфигурация получена"
}

# ─── Шаг 3: SSL-сертификат ───────────────────────────────────────────────────
issue_ssl() {
    step "Выпуск SSL-сертификата Let's Encrypt"
    progress 52 "Запрос SSL-сертификата для ${DOMAIN}…"

    if lsof -iTCP:80 -sTCP:LISTEN -n -P &>/dev/null 2>&1; then
        warn "Порт 80 занят, пробую освободить…"
        systemctl stop nginx apache2 2>/dev/null || true
        sleep 1
    fi

    info "Запрашиваю сертификат (certbot --standalone)…"
    certbot certonly \
        --standalone \
        --non-interactive \
        --agree-tos \
        --email "$LE_EMAIL" \
        -d "$DOMAIN" \
        --quiet \
        || die "certbot завершился с ошибкой. Проверьте, что домен указывает на этот сервер."

    mkdir -p nginx/certs
    cp "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" nginx/certs/fullchain.pem
    cp "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"   nginx/certs/privkey.pem
    chmod 644 nginx/certs/*.pem

    echo "$DOMAIN" > .domain
    ok "Сертификат выпущен → nginx/certs/"
    progress 62 "SSL-сертификат получен и скопирован"
}

# ─── Шаг 4: Авторевыпуск сертификата ────────────────────────────────────────
setup_renewal() {
    step "Настройка автоматического перевыпуска сертификата"

    local renew_script="${PROJECT_DIR}/scripts/renew_cert.sh"
    cat > "$renew_script" <<RENEW
#!/usr/bin/env bash
# Авторевыпуск Let's Encrypt — вызывается cron
set -euo pipefail
PROJECT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="\$(cat "\$PROJECT_DIR/.domain")"

certbot renew \\
    --standalone \\
    --pre-hook  "cd \$PROJECT_DIR && /usr/bin/docker compose stop nginx" \\
    --post-hook "cd \$PROJECT_DIR && \\
        cp /etc/letsencrypt/live/\$DOMAIN/fullchain.pem \$PROJECT_DIR/nginx/certs/fullchain.pem && \\
        cp /etc/letsencrypt/live/\$DOMAIN/privkey.pem   \$PROJECT_DIR/nginx/certs/privkey.pem && \\
        chmod 644 \$PROJECT_DIR/nginx/certs/*.pem && \\
        /usr/bin/docker compose start nginx" \\
    --quiet

echo "\$(date): сертификат для \$DOMAIN проверен/обновлён" >> "\$PROJECT_DIR/logs/certbot.log"
RENEW
    chmod +x "$renew_script"

    local cron_line="15 3 * * * root ${renew_script} >> /var/log/certbot-video-bot.log 2>&1"
    echo "$cron_line" > /etc/cron.d/video-bot-certbot
    chmod 644 /etc/cron.d/video-bot-certbot

    ok "Cron-задание создано: /etc/cron.d/video-bot-certbot (ежедневно в 03:15)"
    progress 65 "Автоперевыпуск сертификата настроен"
}

# ─── Шаг 5: Nginx конфиг ─────────────────────────────────────────────────────
write_nginx_conf() {
    step "Запись конфигурации Nginx"
    mkdir -p nginx/www nginx/certs
    cat > nginx/video-bot.conf <<NGINX
limit_req_zone \$binary_remote_addr zone=telegram_webhook_limit:10m rate=30r/s;

upstream video_bot_backend {
    server bot:8080;
}

# HTTP → HTTPS redirect + ACME challenge
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

# HTTPS
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 10m;

    location /telegram/webhook {
        limit_req zone=telegram_webhook_limit burst=60 nodelay;

        proxy_pass         http://video_bot_backend;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_connect_timeout 10s;
        proxy_send_timeout    30s;
        proxy_read_timeout    30s;
    }

    location /health {
        proxy_pass http://video_bot_backend/health;
    }
}
NGINX
    ok "nginx/video-bot.conf записан для ${DOMAIN}"
    progress 68 "Конфигурация Nginx записана"
}

# ─── Шаг 6: Файл .env ────────────────────────────────────────────────────────
write_env() {
    step "Запись .env"
    if [[ -f .env ]]; then
        warn ".env уже существует — создаю резервную копию .env.bak"
        cp .env .env.bak
    fi

    cat > .env <<ENV
# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN=${BOT_TOKEN}
WEBHOOK_BASE_URL=https://${DOMAIN}
WEBHOOK_PATH=/telegram/webhook
WEBHOOK_SECRET=${WEBHOOK_SECRET}

# ── PostgreSQL ────────────────────────────────────────────────────────────────
POSTGRES_DB=${PG_DB}
POSTGRES_USER=${PG_USER}
POSTGRES_PASSWORD=${PG_PASSWORD}
DATABASE_URL=postgresql+psycopg2://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:5432/${PG_DB}

# ── Redis / Celery ────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# ── Пути ─────────────────────────────────────────────────────────────────────
DOWNLOAD_DIR=/app/downloads
COOKIE_DIR=/app/cookies
LOG_DIR=/app/logs

# ── Доступ ────────────────────────────────────────────────────────────────────
ALLOWED_USERS=
ADMIN_USERS=${ADMIN_ID}

# ── Антиспам / лимиты ────────────────────────────────────────────────────────
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_MESSAGES=8
BAN_SECONDS=600
USER_DAILY_LIMIT=50
USER_QUEUE_LIMIT=3
GLOBAL_QUEUE_LIMIT=50

# ── Загрузка / Telegram ───────────────────────────────────────────────────────
DEFAULT_QUALITY=720p
MAX_FILE_MB=50
DOWNLOAD_TIMEOUT_SECONDS=900
MAX_ACTIVE_DOWNLOADS_PER_USER=1
MAX_DOWNLOAD_DURATION_SECONDS=1800

# ── Кэш ──────────────────────────────────────────────────────────────────────
CACHE_TTL_HOURS=168
DELETE_LOCAL_FILE_AFTER_TELEGRAM_CACHE=true

# ── yt-dlp cookies ────────────────────────────────────────────────────────────
USE_COOKIES=true
FACEBOOK_COOKIES_FILE=/app/cookies/facebook.txt
INSTAGRAM_COOKIES_FILE=/app/cookies/instagram.txt
TIKTOK_COOKIES_FILE=/app/cookies/tiktok.txt

# ── Контейнер ─────────────────────────────────────────────────────────────────
APP_HOST=0.0.0.0
APP_PORT=8080
ENV
    chmod 600 .env
    ok ".env записан (права 600)"
    progress 72 "Файл .env создан"
}

# ─── Шаг 7: systemd-служба ───────────────────────────────────────────────────
setup_systemd() {
    step "Создание systemd-службы video-bot"

    local service_file="/etc/systemd/system/video-bot.service"

    cat > "$service_file" <<SERVICE
[Unit]
Description=Video Bot (Docker Compose)
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${PROJECT_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
SERVICE

    systemctl daemon-reload
    systemctl enable video-bot
    ok "Создана служба: ${service_file}"
    ok "Автозапуск при перезагрузке включён (systemctl enable video-bot)"
    progress 76 "systemd-служба video-bot создана и включена"
}

# ─── Шаг 8: Запуск сервисов ──────────────────────────────────────────────────
start_services() {
    step "Сборка Docker-образов"
    progress 78 "Сборка Docker-образов (может занять 3–5 мин.)…"
    warn "Идёт загрузка базовых образов и установка пакетов внутри контейнера…"
    docker compose build
    ok "Образы собраны"
    progress 90 "Docker-образы собраны"

    step "Запуск через systemd (video-bot.service)"
    progress 92 "Запуск всех сервисов…"
    systemctl start video-bot
    ok "Служба video-bot запущена"

    progress 95 "Ожидание готовности бота…"
    info "Ожидаю готовности бота (30 сек.)…"
    sleep 30

    local health
    health="$(curl -sf "http://localhost:8080/health" 2>/dev/null || echo 'недоступен')"
    if echo "$health" | grep -q '"ok"' 2>/dev/null; then
        ok "Health check пройден"
        progress 100 "Установка успешно завершена ✅"
    else
        warn "Health check: ${health} — возможно бот ещё стартует, проверьте логи"
        progress 98 "Сервисы запущены (health check не прошёл — проверьте логи)"
    fi
}

# ─── Итог ────────────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║                    ✅  НАСТРОЙКА ЗАВЕРШЕНА               ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BOLD}Бот:${NC}           https://${DOMAIN}"
    echo -e "  ${BOLD}Webhook:${NC}       https://${DOMAIN}/telegram/webhook"
    echo -e "  ${BOLD}Администратор:${NC} ${ADMIN_ID}"
    echo -e "  ${BOLD}Сертификат:${NC}    /etc/letsencrypt/live/${DOMAIN}/ (авторевыпуск ежедневно 03:15)"
    echo ""
    echo -e "  ${BOLD}Управление службой:${NC}"
    echo -e "    systemctl status video-bot      — статус службы"
    echo -e "    systemctl restart video-bot     — перезапуск всего стека"
    echo -e "    systemctl stop video-bot        — остановка"
    echo -e "    journalctl -u video-bot -f      — логи запуска/остановки"
    echo ""
    echo -e "  ${BOLD}Управление контейнерами:${NC}"
    echo -e "    docker compose logs -f bot      — логи бота"
    echo -e "    docker compose logs -f worker   — логи воркера"
    echo -e "    docker compose ps               — статус контейнеров"
    echo ""
    echo -e "  ${BOLD}Команды администратора (в боте):${NC}"
    echo -e "    /admin            — панель администратора"
    echo -e "    /adduser <id>     — добавить доверенного пользователя"
    echo -e "    /removeuser <id>  — убрать пользователя"
    echo -e "    /listusers        — список доверенных пользователей"
    echo ""
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
    echo -e "\n${BOLD}${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${BLUE}║        Video Bot — Мастер первоначальной настройки         ║${NC}"
    echo -e "${BOLD}${BLUE}╚════════════════════════════════════════════════════════════╝${NC}\n"
    progress 0 "Запуск установки…"

    preflight
    install_deps
    collect_config
    issue_ssl
    setup_renewal
    write_nginx_conf
    write_env
    setup_systemd
    start_services
    print_summary
}

main "$@"
