#!/usr/bin/env bash
set -euo pipefail
cd /opt/tutorbot
mkdir -p backups
sqlite3 data/bot.db ".backup 'backups/bot-$(date +%F).db'"
find backups -name 'bot-*.db' -mtime +30 -delete
