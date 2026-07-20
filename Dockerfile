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
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY caption.txt ./caption.txt

RUN mkdir -p /app/downloads /app/cookies /app/logs

CMD ["python", "-m", "app.bot.main"]
