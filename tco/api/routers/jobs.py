"""Job API — наблюдаемость фоновых операций (DELTA §6.13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from tco.api.deps import (
    AdminDep,
    AnalystDep,
    PaginationDep,
    SessionDep,
    SettingsDep,
    ViewerDep,
)
from tco.api.dispatch import dispatch
from tco.core.enums import AuditAction, JobStatus, JobType
from tco.core.errors import ConflictError
from tco.core.logging import get_logger
from tco.db.models.job import Job, JobEvent
from tco.services import audit
from tco.services import jobs as job_service

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Какая задача каким Celery-вызовом перезапускается.
_RETRY_DISPATCH: dict[str, str] = {
    JobType.ON_DEMAND_CALCULATION.value: "on_demand",
    JobType.SNAPSHOT_REPLAY.value: "replay",
    JobType.MONITORING_SCENARIO.value: "monitoring",
    JobType.EXPORT.value: "export",
}


@router.get("", summary="Список задач")
def list_jobs(
    session: SessionDep,
    _: ViewerDep,
    page: PaginationDep,
    settings: SettingsDep,
    job_type: JobType | None = None,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    scenario_id: str | None = None,
    correlation_id: str | None = None,
    created_after: datetime | None = None,
    active_only: Annotated[bool, Query(description="Только незавершенные задачи")] = False,
) -> dict[str, Any]:
    stmt = select(Job)
    if job_type:
        stmt = stmt.where(Job.job_type == job_type.value)
    if job_status:
        stmt = stmt.where(Job.status == job_status.value)
    if scenario_id:
        stmt = stmt.where(Job.scenario_id == uuid.UUID(scenario_id))
    if correlation_id:
        stmt = stmt.where(Job.correlation_id == correlation_id)
    if created_after:
        stmt = stmt.where(Job.created_at >= created_after)
    if active_only:
        stmt = stmt.where(
            Job.status.in_(
                [
                    JobStatus.PENDING.value,
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RETRYING.value,
                ]
            )
        )

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Job.created_at.desc()).offset(page.offset).limit(page.limit)
    ).all()

    return {
        "items": [job_service.job_payload(row, settings.api_prefix) for row in rows],
        "meta": {
            "page": page.page,
            "page_size": page.page_size,
            "total": total,
            "total_pages": (total + page.page_size - 1) // page.page_size,
        },
    }


@router.get("/{job_id}", summary="Задача по идентификатору")
def get_job(
    job_id: str, session: SessionDep, settings: SettingsDep, _: ViewerDep
) -> dict[str, Any]:
    job = job_service.get_job(session, job_id)
    return job_service.job_payload(job, settings.api_prefix)


@router.get("/{job_id}/events", summary="Хронология задачи")
def job_events(job_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    """Все переходы состояния — основа разбора инцидентов."""
    job = job_service.get_job(session, job_id)
    events = session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at)
    ).all()
    return {
        "job_id": str(job.id),
        "status": job.status,
        "items": [
            {
                "status": event.status,
                "message": event.message,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
        "total": len(events),
    }


@router.post("/{job_id}/cancel", summary="Отменить задачу")
def cancel(
    job_id: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AnalystDep,
) -> dict[str, Any]:
    job = job_service.get_job(session, job_id)
    job_service.cancel_job(session, job, actor=principal.username)

    audit.record(
        session,
        AuditAction.JOB_CANCEL,
        principal=principal,
        object_type="Job",
        object_id=str(job.id),
        summary=f"Отменена задача {job.job_type}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return job_service.job_payload(job, settings.api_prefix)


@router.post(
    "/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Перезапустить задачу",
)
def retry(
    job_id: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Перезапускает завершившуюся задачу.

    Создается новая задача с уточненным ключом идемпотентности — повтор
    не переписывает историю исходной попытки и не создает дубль снимка,
    если эквивалентный снимок уже существует.
    """
    original = job_service.get_job(session, job_id)
    if not original.is_terminal:
        raise ConflictError(
            "Задача еще выполняется — перезапуск возможен только после завершения",
            details={"status": original.status},
        )

    kind = _RETRY_DISPATCH.get(original.job_type)
    if kind is None:
        raise ConflictError(
            f"Задачи типа {original.job_type} не поддерживают перезапуск",
            details={"job_type": original.job_type},
        )

    params = dict(original.params or {})
    handle = job_service.create_job(
        session,
        job_type=JobType(original.job_type),
        idempotency_key=f"{original.idempotency_key}:retry:{uuid.uuid4().hex[:8]}",
        params=params,
        scenario_id=original.scenario_id,
        parent_job_id=original.id,
        created_by=principal.username,
        request_id=getattr(request.state, "request_id", None),
        reuse_terminal=False,
    )
    job = handle.job
    job.market_snapshot_id = original.market_snapshot_id
    job_service.transition(session, job, JobStatus.QUEUED, message="Перезапуск задачи")

    audit.record(
        session,
        AuditAction.JOB_RETRY,
        principal=principal,
        object_type="Job",
        object_id=str(job.id),
        summary=f"Перезапуск задачи {original.job_type}",
        payload={"original_job_id": str(original.id)},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()

    _dispatch_retry(kind, job, session, params, principal.username)
    session.expire_all()
    job = job_service.get_job(session, job.id)

    logger.info(
        "Задача перезапущена",
        job_id=str(job.id),
        original_job_id=str(original.id),
        job_type=original.job_type,
    )
    return job_service.job_payload(job, settings.api_prefix)


def _dispatch_retry(
    kind: str, job: Job, session: Any, params: dict[str, Any], actor: str
) -> None:
    """Ставит перезапуск в очередь согласно типу исходной задачи."""
    job_id = str(job.id)
    if kind == "on_demand":
        from tco.tasks.pipeline import run_on_demand_calculation

        dispatch(
            run_on_demand_calculation,
            job=job,
            session=session,
            job_id=job_id,
            scenario_id=params["scenario_id"],
            profile_id=params.get("profile_id"),
            force_refresh=bool(params.get("force_refresh", False)),
            created_by=actor,
        )
    elif kind == "replay":
        from tco.tasks.pipeline import replay_snapshot_with_profile

        dispatch(
            replay_snapshot_with_profile,
            job=job,
            session=session,
            snapshot_id=params["snapshot_id"],
            profile_id=params["profile_id"],
            created_by=actor,
            job_id=job_id,
        )
    elif kind == "monitoring":
        from tco.tasks.pipeline import refresh_monitoring_scenario

        dispatch(
            refresh_monitoring_scenario,
            job=job,
            session=session,
            scenario_id=params["scenario_id"],
            profile_id=params.get("profile_id"),
            force_refresh=True,
        )
    elif kind == "export":
        from tco.tasks.maintenance import export_dataset

        dispatch(export_dataset, job=job, session=session, job_id=job_id)
