FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno: JS runtime required by yt-dlp EJS to solve YouTube player challenges.
# yt-dlp[default] in requirements.txt bundles yt-dlp-ejs; remote_components
# ejs:github is set in downloader.py for on-demand solver updates.
ENV DENO_INSTALL=/usr/local
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y \
    && deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt \
    # Ночная сборка yt-dlp (pre-release с PyPI): быстрее получает правки под YouTube и др.
    && pip install --no-cache-dir -U --pre "yt-dlp[default,curl-cffi]"

# Chromium + its OS-level deps for app.utils.rezka's Anubis-challenge
# bypass (REZKA_ANTIBOT_BYPASS=true). Only ever launched for rezka.ag
# downloads with that flag on; harmless (just unused disk) otherwise.
# Браузеры кладём вне /root, чтобы их мог запускать непривилегированный пользователь.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright

# Непривилегированный пользователь: процессы бота и воркера не должны работать от root.
RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin app
ENV HOME=/home/app \
    DENO_DIR=/home/app/.cache/deno

COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app caption.txt ./caption.txt

RUN mkdir -p /app/downloads /app/cookies /app/logs /app/run \
    && chown -R app:app /app /home/app

USER app

CMD ["python", "-m", "app.bot.main"]
