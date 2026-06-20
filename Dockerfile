FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno: JS runtime required by yt-dlp to solve YouTube player challenges.
# Without it YouTube extraction is deprecated and some formats are missing.
# Installed to /usr/local/bin so yt-dlp finds it automatically on PATH.
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
