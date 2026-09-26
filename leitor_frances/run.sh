#!/usr/bin/with-contenv bashio
set -e
mkdir -p /data/books /data/covers /data/backups /data/tts_cache
export APP_DATA_DIR="/data"
export HF_HUB_OFFLINE=1   # Kokoro usa só o modelo local, sem travar esperando a rede

bashio::log.info "Iniciando Leitor Francês Contextual..."
cd /app
exec python -m uvicorn app:app --host 0.0.0.0 --port 8099 \
  --proxy-headers --forwarded-allow-ips="*"
