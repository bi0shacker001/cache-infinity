FROM python:3.14-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CACHEINFINITY_CONFIG_DIR=/config \
    CACHEINFINITY_CREDENTIALS_PATH=/config/credentials/users.yaml \
    DB_USER=cacheinfinity \
    DB_PASS=cacheinfinity \
    CACHEINFINITY_DATABASE_URL=postgresql://cacheinfinity:cacheinfinity@cacheinfinity-db/cacheinfinity

ARG UID=1000
ARG GID=1000
RUN addgroup -g "${GID}" cacheinfinity && \
    adduser -D -G cacheinfinity -u "${UID}" cacheinfinity

WORKDIR /app
COPY pyproject.toml README.md SPEC.md setup.cfg setup.py ./
COPY app ./app
COPY tests ./tests
RUN apk add --no-cache --virtual .build-deps build-base libffi-dev openssl-dev && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "WsgiDAV[all]>=4.0" && \
    pip install --no-cache-dir .[dev] && \
    apk del .build-deps && \
    mkdir -p /backend /staging /config && \
    chown -R cacheinfinity:cacheinfinity /app /backend /staging /config

USER cacheinfinity
EXPOSE 8080
ENTRYPOINT ["cacheinfinity"]
CMD ["serve", "--config-dir", "/config", "--credentials", "/config/credentials/users.yaml"]
