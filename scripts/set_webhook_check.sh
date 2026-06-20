#!/usr/bin/env bash
set -e

source .env

curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" | python3 -m json.tool
