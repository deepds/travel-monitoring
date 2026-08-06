"""Проверка живости воркера: разбирается ли очередь.

Прежняя проверка — ``celery inspect ping`` — отвечала из главного процесса и
отвечала исправно, когда все процессы пула заняты намертво. Шестого августа
воркер трижды за сутки вставал так: контейнер числился healthy, в логе тишина,
очередь не разбиралась, а в базе висели соединения ``idle in transaction`` по
шестнадцать минут. Признак живости должен быть про работу, а не про процесс.

Логика: если в очередях ничего нет — воркер здоров, разбирать нечего. Если
задачи есть, но за отведенное время ни одна не завершилась — воркер встал.

Пороговое время берется с запасом от самой долгой законной задачи: сбор ЖД
делает до 16 обращений за картой мест примерно по 8,5 секунды, плюс повторы.
"""

from __future__ import annotations

import os
import sys

STALE_SECONDS = int(os.environ.get("WORKER_HEALTH_STALE_SECONDS", "900"))
QUEUES = [
    queue.strip()
    for queue in os.environ.get("CELERY_QUEUES", "collect,compute").split(",")
    if queue.strip()
]


def _queued_tasks() -> int:
    """Сколько задач ждет в очередях брокера."""
    import redis

    client = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://redis:6379/0"), socket_timeout=5
    )
    # Celery хранит очередь списком под именем самой очереди.
    return sum(int(client.llen(queue)) for queue in QUEUES)


def _seconds_since_last_finish() -> float | None:
    """Сколько прошло с завершения последней задачи. ``None`` — задач не было."""
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT EXTRACT(EPOCH FROM (now() - max(finished_at))) FROM jobs "
                    "WHERE finished_at IS NOT NULL"
                )
            ).scalar()
    finally:
        engine.dispose()
    return float(row) if row is not None else None


def main() -> int:
    try:
        pending = _queued_tasks()
    except Exception as exc:  # noqa: BLE001 — недоступный брокер проверяет свой healthcheck
        print(f"Брокер недоступен: {exc}", file=sys.stderr)
        return 1

    if pending == 0:
        print("Очередь пуста, разбирать нечего")
        return 0

    try:
        idle = _seconds_since_last_finish()
    except Exception as exc:  # noqa: BLE001
        print(f"База недоступна: {exc}", file=sys.stderr)
        return 1

    # Задач в базе еще не было — на свежей установке это нормально.
    if idle is None:
        print(f"В очереди {pending}, завершенных задач еще нет")
        return 0

    if idle > STALE_SECONDS:
        print(
            f"В очереди {pending} задач, последняя завершилась {idle / 60:.0f} мин назад "
            f"при пороге {STALE_SECONDS / 60:.0f} мин — воркер не разбирает очередь",
            file=sys.stderr,
        )
        return 1

    print(f"В очереди {pending}, последнее завершение {idle / 60:.1f} мин назад")
    return 0


if __name__ == "__main__":
    sys.exit(main())
