FROM python:3.14

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CACHEINFINITY_CONFIG_DIR=/config \
    DB_USER=cacheinfinity \
    DB_PASS=cacheinfinity \
    CACHEINFINITY_DATABASE_URL=postgresql://cacheinfinity:cacheinfinity@cacheinfinity-db/cacheinfinity

ARG UID=1000
ARG GID=1000
RUN groupadd -g "${GID}" cacheinfinity && \
    useradd -m -u "${UID}" -g "${GID}" cacheinfinity

WORKDIR /app
COPY pyproject.toml README.md SPEC.md setup.cfg setup.py ./
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
        libcurl4-openssl-dev \
        python3-dev \
        curl && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "WsgiDAV[all]>=4.0"
COPY app/ ./
RUN pip install --no-cache-dir \
        PyYAML>=6.0 \
        cheroot>=10.0 \
        watchdog>=4.0 \
        psycopg[binary]>=3.2 \
        pytest>=8.0 \
        requests>=2.0 \
        beautifulsoup4>=4.0 \
        pycurl>=7.45.7 \
<<<<<<< HEAD
        Flask>=3.0 \
        rclone-python>=1.0 && \
=======
        Flask>=3.0 && \
>>>>>>> 1faf9bc3cc2ee4f0576fabe70ebe2ec5feb869f1
    mkdir -p /datadir /staging /config && \
    chown -R cacheinfinity:cacheinfinity /app /datadir /staging /config

USER cacheinfinity
EXPOSE 8080
ENTRYPOINT ["python", "/app/cacheinfinity.py"]
CMD ["--config-dir", "/config"]