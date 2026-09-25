ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

WORKDIR /app

RUN apk add --no-cache \
    sqlite \
    unzip \
    curl \
    libstdc++ \
    libgomp \
    && python3 -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt

RUN python3 -m pip install --no-cache-dir -r /app/requirements.txt

# A aplicação está em rootfs/app/
COPY rootfs/app/ /app/

COPY run.sh /run.sh

RUN chmod a+x /run.sh

ENV PYTHONUNBUFFERED=1

CMD ["/run.sh"]
