"""Административное управление мониторингом (DELTA §6.7).

Плановые снимки создаются Celery Beat с интервалом ``SNAPSHOT_INTERVAL_HOURS`` (по умолчанию ежечасно). Эндпоинты ниже дают
администратору возможность запустить внеочередной прогон и наблюдать за ним.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tco.api.deps import AdminDep, PaginationDep, SessionDep, SettingsDep, get_or_404
from tco.api.dispatch import dispatch
from tco.core.enums import AuditAction, JobStatus, JobType, ScenarioType
from tco.core.errors import ConflictError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.job import Job
from tco.db.models.scenario import TravelScenario
from tco.services import audit
from tco.services import jobs as job_service

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/monitoring", tags=["admin", "monitoring"])

_MONITORING_JOB_TYPES = (JobType.MONITORING_BATCH.value, JobType.MONITORING_SCENARIO.value)


class MonitoringRunRequest(BaseModel):
    """Параметры внеочередного прогона мониторинга."""

    profile_id: str | None = Field(None, description="Профиль; по умолчанию активный")
    force_refresh: bool = Field(
        False, description="Игнорировать идемпотентность текущего 6-часового окна"
    )
    limit: int | None = Field(None, ge=1, le=1000, description="Ограничить число сценариев")


@router.post(
    "/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить прогон всех сценариев мониторинга",
)
def run_all(
    payload: MonitoringRunRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Внеочередной прогон мониторинга.

    Без ``force_refresh`` повторный запуск в том же 6-часовом окне не создаст
    дублирующие снимки — вернется уже существующая задача.
    """
    today = utcnow().date()
    scenarios = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.scenario_type == ScenarioType.MONITORING.value)
        .where(TravelScenario.is_active.is_(True))
        .where(TravelScenario.deleted_at.is_(None))
    ).all()
    active_count = sum(1 for item in scenarios if item.is_active_on(today))

    audit.record(
        session,
        AuditAction.MONITORING_RUN,
        principal=principal,
        object_type="MonitoringBatch",
        summary=f"Запущен прогон мониторинга ({active_count} сценариев)",
        payload={"force_refresh": payload.force_refresh, "limit": payload.limit},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()

    from tco.tasks.pipeline import refresh_all_monitoring_scenarios

    # Задача сама создает batch-Job, поэтому связывать нечего.
    result = dispatch(
        refresh_all_monitoring_scenarios,
        job=None,
        session=session,
        profile_id=payload.profile_id,
        force_refresh=payload.force_refresh,
        limit=payload.limit,
    )

    logger.info(
        "Запущен административный прогон мониторинга",
        actor=principal.username,
        scenario_count=active_count,
        force_refresh=payload.force_refresh,
    )
    return {
        "status": "DISPATCHED",
        "celery_task_id": str(result.id),
        "eligible_scenario_count": active_count,
        "force_refresh": payload.force_refresh,
        "jobs_url": f"{settings.api_prefix}/admin/monitoring/jobs",
    }


@router.post(
    "/scenarios/{scenario_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить мониторинг одного сценария",
)
def run_scenario(
    scenario_id: str,
    payload: MonitoringRunRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> dict[str, Any]:
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    if scenario.is_deleted:
        raise ConflictError("Сценарий удален")

    audit.record(
        session,
        AuditAction.MONITORING_RUN,
        principal=principal,
        object_type="TravelScenario",
        object_id=str(scenario.id),
        summary=f"Запущен мониторинг сценария {scenario.code}",
        payload={"force_refresh": payload.force_refresh},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()

    from tco.tasks.pipeline import refresh_monitoring_scenario

    result = dispatch(
        refresh_monitoring_scenario,
        job=None,
        session=session,
        scenario_id=str(scenario.id),
        profile_id=payload.profile_id,
        force_refresh=payload.force_refresh,
    )
    return {
        "status": "DISPATCHED",
        "celery_task_id": str(result.id),
        "scenario": {"id": str(scenario.id), "code": scenario.code, "name": scenario.name},
        "jobs_url": f"{settings.api_prefix}/admin/monitoring/jobs?scenario_id={scenario.id}",
    }


@router.get("/jobs", summary="Задачи мониторинга")
def list_monitoring_jobs(
    session: SessionDep,
    _: AdminDep,
    page: PaginationDep,
    settings: SettingsDep,
    scenario_id: str | None = None,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    stmt = select(Job).where(Job.job_type.in_(_MONITORING_JOB_TYPES))
    if scenario_id:
        stmt = stmt.where(Job.scenario_id == uuid.UUID(scenario_id))
    if job_status:
        stmt = stmt.where(Job.status == job_status.value)

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


@router.get("/jobs/{job_id}", summary="Задача мониторинга")
def get_monitoring_job(
    job_id: str, session: SessionDep, settings: SettingsDep, _: AdminDep
) -> dict[str, Any]:
    job = job_service.get_job(session, job_id)
    payload = job_service.job_payload(job, settings.api_prefix)

    if job.job_type == JobType.MONITORING_BATCH.value:
        children = session.scalars(select(Job).where(Job.parent_job_id == job.id)).all()
        payload["children"] = [
            job_service.job_payload(child, settings.api_prefix) for child in children
        ]
        payload["children_summary"] = _summarize(children)
    return payload


@router.get("/status", summary="Состояние мониторинга")
def monitoring_status(
    session: SessionDep, _: AdminDep, settings: SettingsDep
) -> dict[str, Any]:
    """Сводка: сколько сценариев активно и когда был последний прогон."""
    today = utcnow().date()
    scenarios = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.scenario_type == ScenarioType.MONITORING.value)
        .where(TravelScenario.deleted_at.is_(None))
    ).all()
    active = [item for item in scenarios if item.is_active_on(today)]

    last_batch = session.scalars(
        select(Job)
        .where(Job.job_type == JobType.MONITORING_BATCH.value)
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()

    return {
        "scenario_total": len(scenarios),
        "scenario_active": len(active),
        "snapshot_interval_hours": settings.snapshot_interval_hours,
        "snapshots_per_day": max(1, 24 // max(1, settings.snapshot_interval_hours)),
        "last_batch": job_service.job_payload(last_batch, settings.api_prefix)
        if last_batch
        else None,
        "kpi_target_scenarios": 100,
        "meets_coverage_target": len(active) >= 100,
    }


def _summarize(jobs: list[Job]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for job in jobs:
        summary[job.status] = summary.get(job.status, 0) + 1
    return summary
