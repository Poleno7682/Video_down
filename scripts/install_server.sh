#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx

sudo systemctl enable docker
sudo systemctl start docker

echo "Docker and nginx/certbot installed."
echo "Next:"
echo "1) cp .env.example .env"
echo "2) nano .env"
echo "3) edit nginx/video-bot.conf server_name"
echo "4) docker compose up -d --build"
