"""Жизненный цикл фоновых задач.

Задача создается в API, исполняется в Celery-воркере и всё это время
остается наблюдаемой через ``GET /api/v1/jobs/{id}``. Идемпотентность
гарантируется ключом: повторный запуск в том же окне возвращает уже
существующую задачу вместо создания дубля (DELTA §4.5, §11.2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from tco.core.enums import JobStatus, JobType
from tco.core.errors import ConflictError, NotFoundError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.job import Job, JobEvent

logger = get_logger(__name__)


@dataclass(slots=True)
class JobHandle:
    job: Job
    created: bool

    @property
    def id(self) -> uuid.UUID:
        return self.job.id


def create_job(
    session: Session,
    *,
    job_type: JobType,
    idempotency_key: str,
    params: dict[str, Any] | None = None,
    scenario_id: uuid.UUID | None = None,
    parent_job_id: uuid.UUID | None = None,
    created_by: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    max_attempts: int = 3,
    timeout_seconds: int | None = None,
    queue: str | None = None,
    reuse_terminal: bool = True,
) -> JobHandle:
    """Создает задачу либо возвращает существующую с тем же ключом.

    ``reuse_terminal=False`` заставляет создать новую задачу, даже если
    предыдущая с тем же ключом уже завершилась (используется для retry).
    """
    existing = session.scalars(
        select(Job).where(Job.idempotency_key == idempotency_key)
    ).first()
    if existing is not None:
        if not existing.is_terminal or reuse_terminal:
            return JobHandle(job=existing, created=False)
        # Терминальная задача переиспользуется с уточненным ключом.
        idempotency_key = f"{idempotency_key}:{uuid.uuid4().hex[:8]}"

    now = utcnow()
    job = Job(
        id=uuid.uuid4(),
        job_type=job_type.value,
        status=JobStatus.PENDING.value,
        idempotency_key=idempotency_key,
        scenario_id=scenario_id,
        parent_job_id=parent_job_id,
        params=params or {},
        result={},
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
        queue=queue,
        created_by=created_by,
        request_id=request_id,
        correlation_id=correlation_id or (request_id or uuid.uuid4().hex),
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    add_event(session, job, JobStatus.PENDING, "Задача создана")
    return JobHandle(job=job, created=True)


def add_event(
    session: Session,
    job: Job,
    status: JobStatus,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> JobEvent:
    event = JobEvent(
        job_id=job.id,
        status=status.value,
        message=(message or "")[:2048] or None,
        payload=payload or {},
        created_at=utcnow(),
    )
    session.add(event)
    return event


def transition(
    session: Session,
    job: Job,
    status: JobStatus,
    *,
    message: str | None = None,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Job:
    """Переводит задачу в новое состояние и фиксирует событие."""
    now = utcnow()
    # Терминальный статус не всегда последний: задачу, ошибочно признанную
    # зависшей, следующий шаг конвейера доводит до SUCCESS. В родительской
    # пачке она при этом должна отметиться один раз, а не дважды.
    was_terminal = job.is_terminal
    job.status = status.value
    job.updated_at = now
    job.heartbeat_at = now

    if status == JobStatus.QUEUED and job.queued_at is None:
        job.queued_at = now
    if status == JobStatus.RUNNING:
        if job.started_at is None:
            job.started_at = now
        job.attempts = int(job.attempts or 0) + 1
    if status.is_terminal:
        job.finished_at = now
    if result is not None:
        job.result = result
    if error_code:
        job.error_code = error_code[:64]
    if error_message:
        job.error_message = error_message[:4096]

    add_event(session, job, status, message, payload)
    session.flush()

    if status.is_terminal and not was_terminal and job.parent_job_id is not None:
        report_child_finished(session, job.parent_job_id)

    return job


#: Статусы, в которых пачка еще принимает отметки о завершении детей.
_LIVE_STATUSES = (JobStatus.RUNNING.value, JobStatus.RETRYING.value)


def report_child_finished(session: Session, parent_job_id: uuid.UUID | str | None) -> Job | None:
    """Отмечает в родительской пачке, что дочерняя задача дошла до конца.

    После диспетчеризации пачка не делает ничего сама: работу выполняют дети.
    Без этой отметки ее ``heartbeat_at`` замирает на моменте запуска, и
    ``detect_stalled_jobs`` через четверть часа объявляет живой прогон
    зависшим — а закончиться пачке нечем, она так и остается незакрытой.
    Отметка двигает прогресс и продлевает heartbeat одним действием.

    Инкремент делается одним ``UPDATE``: сценарии считаются восемью процессами
    параллельно, и чтение с последующей записью теряло бы отметки. Условие по
    статусу заодно делает закрытие однократным — до итога доходит ровно один
    ребенок, остальные попадают уже в терминальную пачку и ничего не меняют.

    Возвращает пачку, если этой отметкой она закрылась.
    """
    if parent_job_id is None:
        return None

    parent_id = uuid.UUID(str(parent_job_id))
    now = utcnow()
    row = session.execute(
        update(Job)
        .where(Job.id == parent_id)
        .where(Job.status.in_(_LIVE_STATUSES))
        .values(
            progress_current=Job.progress_current + 1,
            heartbeat_at=now,
            updated_at=now,
        )
        .returning(Job.progress_current, Job.progress_total)
        .execution_options(synchronize_session=False)
    ).first()
    if row is None:
        return None

    current, total = row
    if not total or current < total:
        return None

    parent = session.get(Job, parent_id, populate_existing=True)
    if parent is None or parent.is_terminal:
        return None

    by_status = dict(
        session.execute(
            select(Job.status, func.count())
            .where(Job.parent_job_id == parent_id)
            .group_by(Job.status)
        ).all()
    )
    return transition(
        session,
        parent,
        JobStatus.SUCCESS,
        message=f"Завершены все {total} дочерних задач",
        result={**parent.result, "children_by_status": by_status},
    )


def set_progress(
    session: Session,
    job: Job,
    *,
    current: int,
    total: int,
    message: str | None = None,
) -> None:
    job.progress_current = current
    job.progress_total = total
    job.progress_message = (message or "")[:512] or None
    job.heartbeat_at = utcnow()
    job.updated_at = job.heartbeat_at
    session.flush()


def get_job(session: Session, job_id: uuid.UUID | str) -> Job:
    job = session.get(Job, uuid.UUID(str(job_id)))
    if job is None:
        raise NotFoundError(f"Задача {job_id} не найдена")
    return job


def cancel_job(session: Session, job: Job, *, actor: str | None = None) -> Job:
    if job.is_terminal:
        raise ConflictError(
            f"Задача уже завершена со статусом {job.status}",
            details={"status": job.status},
        )
    return transition(
        session,
        job,
        JobStatus.CANCELLED,
        message=f"Отменена пользователем {actor or 'system'}",
    )


def detect_stalled_jobs(
    session: Session,
    *,
    stale_after_seconds: int = 900,
    queued_stale_after_seconds: int = 4 * 3600,
) -> list[Job]:
    """Находит зависшие задачи и переводит их в ``TIMED_OUT``.

    Признак зависания — отсутствие heartbeat дольше порога при незавершенном
    статусе (DELTA §11.2 «зависшая задача обнаруживается»).

    Порогов два, потому что ожидание очереди — не зависание. Суточный прогон
    сетки диспетчеризует 1703 сценария за минуты, а собирает их по лимиту
    темпа источников больше часа, и по общему порогу большинство живых задач
    объявлялось бы мертвыми. Из очереди задача все же должна выбраться за
    смену: иначе это потерянная задача, а не ждущая.
    """
    now = utcnow()
    cutoff = now - timedelta(seconds=stale_after_seconds)
    queued_cutoff = now - timedelta(seconds=queued_stale_after_seconds)
    candidates = session.scalars(
        select(Job)
        .where(Job.heartbeat_at.is_not(None))
        .where(
            or_(
                and_(
                    Job.status.in_([JobStatus.RUNNING.value, JobStatus.RETRYING.value]),
                    Job.heartbeat_at < cutoff,
                ),
                and_(Job.status == JobStatus.QUEUED.value, Job.heartbeat_at < queued_cutoff),
            )
        )
    ).all()

    stalled: list[Job] = []
    for job in candidates:
        transition(
            session,
            job,
            JobStatus.TIMED_OUT,
            message=f"Нет признаков активности дольше {stale_after_seconds} с",
            error_code="STALLED",
            error_message="Задача признана зависшей планировщиком",
        )
        stalled.append(job)

    if stalled:
        logger.warning("Обнаружены зависшие задачи", count=len(stalled))
    return stalled


def job_payload(job: Job, api_prefix: str = "/api/v1") -> dict[str, Any]:
    """Представление задачи для API."""
    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "scenario_id": str(job.scenario_id) if job.scenario_id else None,
        "market_snapshot_id": str(job.market_snapshot_id) if job.market_snapshot_id else None,
        "scenario_run_id": str(job.scenario_run_id) if job.scenario_run_id else None,
        "parent_job_id": str(job.parent_job_id) if job.parent_job_id else None,
        "params": job.params,
        "result": job.result,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "progress": {
            "current": job.progress_current,
            "total": job.progress_total,
            "ratio": job.progress_ratio,
            "message": job.progress_message,
        },
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "created_at": job.created_at.isoformat(),
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_by": job.created_by,
        "correlation_id": job.correlation_id,
        "status_url": f"{api_prefix}/jobs/{job.id}",
    }
