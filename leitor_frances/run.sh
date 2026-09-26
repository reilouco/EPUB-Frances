#!/usr/bin/env bashio
set -e
mkdir -p /data/books /data/covers /data/backups

export APP_DATA_DIR="/data"
export MAX_UPLOAD_MB="$(bashio::config 'max_upload_mb')"
export SHOW_CONTEXT_DEFAULT="$(bashio::config 'mostrar_contexto_por_padrao')"

bashio::log.info "Iniciando Leitor Francês Contextual..."
cd /app
exec python -m uvicorn app:app --host 0.0.0.0 --port 8099 \
  --proxy-headers --forwarded-allow-ips="*"
