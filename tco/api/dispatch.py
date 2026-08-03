"""Постановка фоновых задач из API.

Недоступность брокера — штатный сбой, а не внутренняя ошибка: задача
помечается неуспешной с понятным кодом, а клиент получает ``503`` и может
повторить запрос. Иначе задача навсегда осталась бы в ``QUEUED``, ожидая
воркер, который никогда о ней не узнает.
"""

from __future__ import annotations

from typing import Any, Callable

from kombu.exceptions import OperationalError
from sqlalchemy.orm import Session

from tco.core.enums import JobStatus
from tco.core.errors import BrokerUnavailableError
from tco.core.logging import get_logger
from tco.db.models.job import Job
from tco.services import jobs as job_service

logger = get_logger(__name__)

#: Ошибки, означающие недоступность брокера, а не дефект задачи.
_BROKER_ERRORS = (OperationalError, ConnectionRefusedError, ConnectionError, OSError)


def dispatch(task: Callable[..., Any], *, job: Job | None, session: Session, **kwargs: Any) -> Any:
    """Ставит задачу в очередь, корректно обрабатывая недоступность брокера.

    При сбое связанная ``Job`` переводится в ``FAILED`` с кодом
    ``BROKER_UNAVAILABLE``, после чего поднимается ``BrokerUnavailableError``.
    """
    try:
        return task.delay(**kwargs)
    except _BROKER_ERRORS as exc:
        logger.error(
            "Не удалось поставить задачу в очередь — брокер недоступен",
            task=getattr(task, "name", str(task)),
            job_id=str(job.id) if job is not None else None,
            error=str(exc),
        )
        if job is not None:
            job_service.transition(
                session,
                job,
                JobStatus.FAILED,
                message="Брокер задач недоступен",
                error_code="BROKER_UNAVAILABLE",
                error_message=str(exc)[:4096],
            )
            session.commit()
        raise BrokerUnavailableError(
            "Очередь задач недоступна — операция не запущена, повторите запрос позже",
            details={"job_id": str(job.id) if job is not None else None},
        ) from exc
