ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

ENV PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv espeak-ng libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt /app/requirements.txt

# torch CPU-only (evita baixar vários GB de CUDA)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install -r /app/requirements.txt

# Modelo de tradução (Argos)
COPY build_argos_model.py /app/
RUN python /app/build_argos_model.py

# Modelo de voz (Kokoro): se o download falhar, o build continua
# e o modelo é baixado no primeiro uso
RUN python -c "from kokoro import KPipeline; \
p = KPipeline(lang_code='f', repo_id='hexgrad/Kokoro-82M'); \
[None for _ in p('Bonjour.', voice='ff_siwis')]; \
print('Kokoro pronto.')" \
    || echo 'AVISO: Kokoro não pré-carregado; será baixado no primeiro uso.'

COPY rootfs/app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
