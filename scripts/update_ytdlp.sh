#!/usr/bin/env bash
set -e

docker compose exec worker pip install -U yt-dlp
docker compose restart worker
