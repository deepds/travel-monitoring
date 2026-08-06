"""Проверка живости воркера по факту разбора очереди.

``celery inspect ping`` отвечает из главного процесса, и отвечает исправно,
когда все процессы пула заняты намертво. Шестого августа это и наблюдалось:
контейнер числился здоровым, в логе стояла тишина, очередь не разбиралась, а
лечилось только перезапуском руками. Проверка «процесс жив» для воркера
бессмысленна: он жив всегда, вопрос в том, движется ли работа.

Здесь проверяется движение. Если в очередях воркера есть задачи, а за окно
наблюдения ни одна не завершилась, воркер объявляется нездоровым — дальше его
поднимает ``autoheal``.

Запускается как ``python -m tco.tasks.health``; очереди берутся из
``CELERY_QUEUES``, то есть те же, что слушает сам воркер.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from sqlalchemy import func, select

from tco.core.config import get_settings
from tco.core.utils import utcnow

#: Сколько ждать завершения хотя бы одной задачи, прежде чем считать застоем.
#:
#: Законная длительность одной задачи сбора ограничена пределом времени в
#: 300 секунд, поэтому за четверть часа при живом пуле обязана закрыться хотя
#: бы одна. Меньшее окно давало бы ложные срабатывания на длинных сборах ЖД,
#: где карта мест — отдельное обращение на каждый поезд.
DEFAULT_STALE_MINUTES = 15


def _queue_depth(queues: list[str]) -> tuple[int, dict[str, int]]:
    """Сколько задач ждет разбора. Очередь Celery в Redis — обычный список."""
    settings = get_settings()
    if not settings.redis_url:
        return 0, {}
    import redis as redis_lib

    client = redis_lib.Redis.from_url(
        settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0
    )
    try:
        depths = {name: int(client.llen(name)) for name in queues}
    finally:
        client.close()
    return sum(depths.values()), depths


def _last_finished_age_minutes() -> float | None:
    """Сколько минут назад завершилась последняя задача. ``None`` — никогда."""
    from tco.db.models.job import Job
    from tco.db.session import session_scope

    with session_scope() as session:
        last = session.scalar(select(func.max(Job.finished_at)))
    if last is None:
        return None
    return (utcnow() - last).total_seconds() / 60.0


def check(
    queues: list[str], *, stale_minutes: int = DEFAULT_STALE_MINUTES
) -> tuple[bool, dict[str, Any]]:
    """``(здоров, подробности)``.

    Пустая очередь — здоров: разбирать нечего, и молчание воркера законно.
    """
    pending, depths = _queue_depth(queues)
    details: dict[str, Any] = {"queues": depths, "pending": pending}
    if pending == 0:
        details["verdict"] = "очередь пуста"
        return True, details

    age = _last_finished_age_minutes()
    details["last_finished_minutes_ago"] = round(age, 1) if age is not None else None
    if age is None:
        # Ни одной завершенной задачи за всю историю — это свежая установка,
        # а не застой. Объявлять ее больной значило бы уронить контейнер сразу
        # после развертывания.
        details["verdict"] = "история задач пуста"
        return True, details
    if age > stale_minutes:
        details["verdict"] = (
            f"очередь не разбирается: {pending} задач ждут, "
            f"последняя завершилась {age:.0f} мин назад"
        )
        return False, details

    details["verdict"] = "очередь разбирается"
    return True, details


def main() -> int:
    queues = [
        item.strip()
        for item in os.environ.get("CELERY_QUEUES", "collect,compute").split(",")
        if item.strip()
    ]
    stale = int(os.environ.get("WORKER_STALE_MINUTES", DEFAULT_STALE_MINUTES))
    try:
        healthy, details = check(queues, stale_minutes=stale)
    except Exception as exc:  # noqa: BLE001 — недоступность зависимостей это тоже диагноз
        print(f"Проверка не выполнена: {exc}", file=sys.stderr)
        return 1
    print(details.get("verdict", ""), details)
    return 0 if healthy else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
