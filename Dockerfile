FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# yt-dlp auto-discovers extractor plugins by scanning sys.path for a
# yt_dlp_plugins package. `python -m app.bot.main` (the bot's entrypoint)
# adds cwd to sys.path automatically, but the worker's entrypoint is the
# `celery` console script, which does NOT — so without this, the worker
# process silently never finds yt_dlp_plugins/extractor/rezka.py while the
# bot process does. Setting PYTHONPATH explicitly makes both consistent
# regardless of how each process is launched.
ENV PYTHONPATH=/app

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
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY caption.txt ./caption.txt
# yt-dlp auto-discovers extractor plugins under ./yt_dlp_plugins relative to
# the process's cwd (WORKDIR /app here) — see yt_dlp_plugins/extractor/rezka.py.
COPY yt_dlp_plugins ./yt_dlp_plugins

RUN mkdir -p /app/downloads /app/cookies /app/logs

CMD ["python", "-m", "app.bot.main"]
