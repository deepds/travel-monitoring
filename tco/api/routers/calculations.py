"""On-demand расчет (DELTA §6.6).

Долгий расчет не блокирует API: запрос возвращает ``202 Accepted`` и ``job_id``.
Если подходящий результат есть в кэше, возвращается ``200 OK`` с готовым
``ScenarioRun`` — целевое время ответа не более 2 секунд.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from tco.api.deps import AnalystDep, SessionDep, SettingsDep, ViewerDep
from tco.api.dispatch import dispatch
from tco.api.routers.scenarios import ScenarioCreate
from tco.api.serializers import run_full, scenario_brief, snapshot_brief
from tco.cache.result_cache import get_result_cache
from tco.core.enums import JobStatus, JobType, RunType
from tco.core.errors import NotFoundError, ValidationError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot
from tco.engine.fingerprint import cache_key as build_cache_key
from tco.engine.fingerprint import job_idempotency_key
from tco.services import jobs as job_service
from tco.services.calculation import active_profile, scenario_key, validate
from tco.services.scenarios import create_scenario

logger = get_logger(__name__)

router = APIRouter(prefix="/calculations", tags=["calculations"])


class CalculationRequest(BaseModel):
    """Запрос расчета: либо готовый сценарий, либо его параметры."""

    scenario_id: str | None = Field(None, description="Идентификатор существующего сценария")
    scenario: ScenarioCreate | None = Field(None, description="Параметры нового сценария")
    profile_id: str | None = Field(None, description="Профиль расчета; по умолчанию активный")
    force_refresh: bool = Field(False, description="Игнорировать кэш и собрать данные заново")


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить расчет сценария",
)
def create_calculation(
    payload: CalculationRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: AnalystDep,
) -> dict[str, Any]:
    """Ставит расчет в очередь либо отдает результат из кэша.

    Невалидный сценарий завершается до обращения к внешним источникам
    (``422`` с перечнем причин).
    """
    scenario = _resolve_scenario(session, payload, principal.username)
    profile = _resolve_profile(session, payload.profile_id, scenario)

    # --- Валидация до внешних запросов ------------------------------------- #
    validation = validate(session, scenario, profile)
    if not validation.is_valid:
        session.commit()
        raise ValidationError(
            "Сценарий не поддерживается",
            code="SCENARIO_UNSUPPORTED",
            details={
                "scenario_id": str(scenario.id),
                "scenario_code": scenario.code,
                **validation.as_dict(),
            },
        )

    # --- Кэш ----------------------------------------------------------------- #
    key = build_cache_key(scenario_key(scenario), profile.version, profile.code)
    if not payload.force_refresh and settings.result_cache_enabled:
        hit = get_result_cache().get(session, key)
        if hit is not None and hit.scenario_run_id:
            run = session.get(ScenarioRun, uuid.UUID(hit.scenario_run_id))
            if run is not None:
                session.commit()
                response.status_code = status.HTTP_200_OK
                logger.info(
                    "Расчет отдан из кэша",
                    scenario=scenario.code,
                    layer=hit.layer,
                    run_id=str(run.id),
                )
                return {
                    "cached": True,
                    "status": run.status,
                    "scenario": scenario_brief(scenario),
                    "run": run_full(run),
                    "cache": {"layer": hit.layer, "age_seconds": round(hit.age_seconds, 1)},
                    "warnings": [issue.as_dict() for issue in validation.warnings],
                }

    # --- Фоновая задача -------------------------------------------------------- #
    request_id = getattr(request.state, "request_id", None)
    handle = job_service.create_job(
        session,
        job_type=JobType.ON_DEMAND_CALCULATION,
        idempotency_key=job_idempotency_key(
            fingerprint=scenario.fingerprint,
            requested_at=utcnow(),
            # On-demand дедуплицируется по TTL кэша, а не по 6-часовому окну.
            bucket_hours=0 if payload.force_refresh else 1,
            profile_version=profile.version,
            run_type=RunType.ON_DEMAND,
            salt="force" if payload.force_refresh else "",
        ),
        params={
            "scenario_id": str(scenario.id),
            "profile_id": str(profile.id),
            "force_refresh": payload.force_refresh,
        },
        scenario_id=scenario.id,
        created_by=principal.username,
        request_id=request_id,
        correlation_id=getattr(request.state, "correlation_id", None),
        timeout_seconds=settings.on_demand_job_timeout_seconds,
    )
    job = handle.job

    if handle.created:
        job_service.transition(session, job, JobStatus.QUEUED, message="Поставлено в очередь")
    session.commit()

    if handle.created:
        from tco.tasks.pipeline import run_on_demand_calculation

        dispatch(
            run_on_demand_calculation,
            job=job,
            session=session,
            job_id=str(job.id),
            scenario_id=str(scenario.id),
            profile_id=str(profile.id),
            force_refresh=payload.force_refresh,
            created_by=principal.username,
        )
        # В eager-режиме задача уже отработала — отдаем актуальное состояние.
        session.expire_all()
        job = job_service.get_job(session, job.id)

    return {
        "job_id": str(job.id),
        "status": job.status,
        "cached": False,
        "status_url": f"{settings.api_prefix}/calculations/{job.id}",
        "scenario": scenario_brief(scenario),
        "profile": profile.label,
        "reused_job": not handle.created,
        "warnings": [issue.as_dict() for issue in validation.warnings],
    }


@router.get("/{job_id}", summary="Состояние и результат расчета")
def get_calculation(
    job_id: str, session: SessionDep, settings: SettingsDep, _: ViewerDep
) -> dict[str, Any]:
    """Состояние задачи. При успехе содержит полный ``ScenarioRun``."""
    job = job_service.get_job(session, job_id)
    if job.job_type != JobType.ON_DEMAND_CALCULATION.value:
        raise NotFoundError(f"Задача {job_id} не является расчетом")

    payload = job_service.job_payload(job, settings.api_prefix)
    payload["status_url"] = f"{settings.api_prefix}/calculations/{job.id}"

    if job.scenario_run_id:
        run = session.get(ScenarioRun, job.scenario_run_id)
        if run is not None:
            payload["run"] = run_full(run)
            payload["cached"] = bool(job.result.get("cached", False))
    if job.market_snapshot_id:
        snapshot = session.get(MarketSnapshot, job.market_snapshot_id)
        if snapshot is not None:
            payload["snapshot"] = snapshot_brief(snapshot)

    return payload


@router.post("/{job_id}/cancel", summary="Отменить расчет")
def cancel_calculation(
    job_id: str, session: SessionDep, settings: SettingsDep, principal: AnalystDep
) -> dict[str, Any]:
    job = job_service.get_job(session, job_id)
    job_service.cancel_job(session, job, actor=principal.username)
    session.commit()
    logger.info("Расчет отменен", job_id=str(job.id), actor=principal.username)
    return job_service.job_payload(job, settings.api_prefix)


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #


def _resolve_scenario(
    session: SessionDep, payload: CalculationRequest, username: str
) -> TravelScenario:
    if payload.scenario_id:
        scenario = session.get(TravelScenario, uuid.UUID(payload.scenario_id))
        if scenario is None or scenario.is_deleted:
            raise NotFoundError(f"Сценарий {payload.scenario_id} не найден")
        return scenario

    if payload.scenario is None:
        raise ValidationError("Необходимо передать scenario_id или параметры сценария")

    draft = payload.scenario.to_draft()
    scenario, _ = create_scenario(session, draft, created_by=username)
    return scenario


def _resolve_profile(
    session: SessionDep, profile_id: str | None, scenario: TravelScenario
) -> CalculationProfile:
    if profile_id:
        profile = session.get(CalculationProfile, uuid.UUID(profile_id))
        if profile is None:
            raise NotFoundError(f"Профиль расчета {profile_id} не найден")
        return profile
    return active_profile(session, scenario)
