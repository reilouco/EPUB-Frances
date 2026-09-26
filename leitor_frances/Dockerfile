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
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
      torch && \
    pip install -r /app/requirements.txt

COPY build_argos_model.py build_kokoro_model.py /app/
RUN python /app/build_argos_model.py && python /app/build_kokoro_model.py

COPY rootfs/app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
