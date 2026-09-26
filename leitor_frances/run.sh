#!/usr/bin/with-contenv bashio
set -e
mkdir -p /data/books /data/covers /data/backups
export APP_DATA_DIR="/data"

bashio::log.info "Iniciando Leitor Francês Contextual..."
cd /app
exec python -m uvicorn app:app --host 0.0.0.0 --port 8099 \
  --proxy-headers --forwarded-allow-ips="*"
