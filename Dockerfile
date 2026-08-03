# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow

WORKDIR /app

# curl нужен для HEALTHCHECK, остальное — минимальный набор для psycopg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*

# Слой зависимостей отделен от кода: правка исходников не пересобирает pip.
COPY pyproject.toml ./
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -c "\
import tomllib, subprocess, sys;\
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'];\
subprocess.check_call([sys.executable,'-m','pip','install',*deps])"

COPY tco ./tco
COPY catalog ./catalog
COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && python -m pip install --no-deps -e . \
    && mkdir -p /data/raw /data/exports \
    # Процессы не должны работать от root.
    && useradd --create-home --uid 10001 tco \
    && chown -R tco:tco /app /data

USER tco

ENV RAW_STORAGE_DIR=/data/raw \
    EXPORT_STORAGE_DIR=/data/exports

# tini корректно пробрасывает сигналы в дочерние процессы (Celery, uvicorn).
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["api"]
