#!/usr/bin/with-contenv bashio

set -e

# Diretórios persistentes: sobrevivem a reinicializações e atualizações do add-on.
mkdir -p /data/books
mkdir -p /data/covers
mkdir -p /data/backups

# Variáveis de configuração disponíveis para a aplicação Python.
export APP_DATA_DIR="/data"
export MAX_UPLOAD_MB="$(bashio::config 'max_upload_mb')"
export SHOW_CONTEXT_DEFAULT="$(bashio::config 'mostrar_contexto_por_padrao')"

bashio::log.info "Iniciando Leitor Francês Contextual..."
bashio::log.info "Livros e dados persistentes: /data"

# O app.py deve estar em /app/app.py dentro do contêiner.
cd /app

# Usa o Python do ambiente virtual criado no Dockerfile.
exec /opt/venv/bin/python -m uvicorn app:app \
  --host 0.0.0.0 \
  --port 8099 \
  --proxy-headers \
  --forwarded-allow-ips="*"
