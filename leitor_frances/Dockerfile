ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-lxml \
    build-base \
    libffi-dev \
    python3-dev \
    linux-headers

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip3 install --no-cache-dir --break-system-packages \
    -r /app/requirements.txt

COPY build_argos_model.py /app/build_argos_model.py

# O modelo é baixado uma única vez durante o build.
# Depois disso, a tradução funciona localmente e offline.
RUN python3 /app/build_argos_model.py

COPY rootfs/app /app

RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]