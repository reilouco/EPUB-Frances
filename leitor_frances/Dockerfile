FROM ghcr.io/home-assistant/base:latest

WORKDIR /app

RUN apk add --no-cache \
    python3 \
    py3-pip \
    sqlite \
    unzip \
    curl \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt

RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# Os arquivos da aplicação estão em rootfs/app no repositório
COPY rootfs/app/ /app/

COPY run.sh /run.sh

RUN chmod a+x /run.sh

ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

CMD ["/run.sh"]
