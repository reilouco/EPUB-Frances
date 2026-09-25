FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache \
    python3 \
    py3-pip \
    sqlite \
    unzip \
    curl \
    tesseract-ocr \
    tesseract-ocr-data-fra \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY run.sh /run.sh

RUN chmod a+x /run.sh \
    && mkdir -p /data/books /data/covers /data/models

CMD ["/run.sh"]
