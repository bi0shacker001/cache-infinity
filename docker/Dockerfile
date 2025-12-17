FROM python:3.14-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CACHEINFINITY_CONFIG_DIR=/config \
    DB_USER=cacheinfinity \
    DB_PASS=cacheinfinity \
    CACHEINFINITY_DATABASE_URL=postgresql://cacheinfinity:cacheinfinity@cacheinfinity-db/cacheinfinity

ARG UID=1000
ARG GID=1000
RUN addgroup -g "${GID}" cacheinfinity && \
    adduser -D -G cacheinfinity -u "${UID}" cacheinfinity

WORKDIR /app
COPY pyproject.toml README.md SPEC.md setup.cfg setup.py ./
RUN apk add --no-cache --virtual .build-deps build-base libffi-dev openssl-dev && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "WsgiDAV[all]>=4.0" && \
    apk del .build-deps
COPY app/ ./
RUN pip install --no-cache-dir \
        PyYAML>=6.0 \
        cheroot>=10.0 \
        watchdog>=4.0 \
        psycopg[binary]>=3.2 \
        pytest>=8.0 \
        requests>=2.0 \
        beautifulsoup4>=4.0 && \
    mkdir -p /backend /staging /config && \
    chown -R cacheinfinity:cacheinfinity /app /backend /staging /config

USER cacheinfinity
EXPOSE 8080
ENTRYPOINT ["python", "/app/cacheinfinity.py"]
CMD ["--config-dir", "/config"]