#!/bin/sh
# Точка входа контейнера. Роль передается первым аргументом.
set -eu

role="${1:-api}"
shift 2>/dev/null || true

wait_for_db() {
    # Миграции и воркеры бессмысленны без БД: ждем ее готовности.
    python - <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL", "")
deadline = time.monotonic() + 90
last = ""
while time.monotonic() < deadline:
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        print("База данных доступна", flush=True)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last = str(exc)
        time.sleep(2)

print(f"База данных недоступна за отведенное время: {last}", file=sys.stderr, flush=True)
sys.exit(1)
PY
}

case "$role" in
    api)
        wait_for_db
        exec uvicorn tco.api.app:app \
            --host 0.0.0.0 --port 8000 \
            --workers "${API_WORKERS:-2}" \
            --proxy-headers --forwarded-allow-ips='*' \
            --no-access-log
        ;;
    worker)
        wait_for_db
        exec celery -A tco.tasks.celery_app:celery_app worker \
            --loglevel="${CELERY_LOG_LEVEL:-info}" \
            --concurrency="${CELERY_CONCURRENCY:-4}" \
            --queues="${CELERY_QUEUES:-collect,compute,ondemand,maintenance}" \
            --hostname="worker@%h"
        ;;
    beat)
        wait_for_db
        exec celery -A tco.tasks.celery_app:celery_app beat \
            --loglevel="${CELERY_LOG_LEVEL:-info}" \
            --schedule=/tmp/celerybeat-schedule
        ;;
    migrate)
        wait_for_db
        exec alembic upgrade head
        ;;
    bootstrap)
        wait_for_db
        alembic upgrade head
        exec python -m tco.cli bootstrap
        ;;
    shell)
        exec python "$@"
        ;;
    *)
        exec "$role" "$@"
        ;;
esac
