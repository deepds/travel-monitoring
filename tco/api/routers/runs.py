"""Scenario Runs — неизменяемые результаты расчета (DELTA §6.9)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from tco.api.deps import PaginationDep, SessionDep, ViewerDep, get_or_404
from tco.api.serializers import run_brief, run_full, scenario_full, snapshot_brief
from tco.core.enums import ConfidenceLevel, RunStatus, RunType, TransportType
from tco.core.logging import get_logger
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot
from tco.version import METRIC_DISCLAIMER_RU, METRIC_TITLE_RU

logger = get_logger(__name__)

router = APIRouter(prefix="/scenario-runs", tags=["scenario-runs"])


@router.get("", summary="Список расчетов")
def list_runs(
    session: SessionDep,
    _: ViewerDep,
    page: PaginationDep,
    scenario_id: str | None = None,
    scenario_code: str | None = None,
    snapshot_id: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    run_type: RunType | None = None,
    confidence_level: ConfidenceLevel | None = None,
    origin: str | None = None,
    destination: str | None = None,
    transport_type: TransportType | None = None,
    observation_date_from: date | None = None,
    observation_date_to: date | None = None,
    profile_version: str | None = None,
    min_quality_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    complete_only: bool = False,
    include_synthetic: bool = True,
) -> dict[str, Any]:
    stmt = select(ScenarioRun)
    needs_scenario_join = any([origin, destination, transport_type, scenario_code])
    if needs_scenario_join:
        stmt = stmt.join(TravelScenario, ScenarioRun.scenario_id == TravelScenario.id)

    if scenario_id:
        stmt = stmt.where(ScenarioRun.scenario_id == uuid.UUID(scenario_id))
    if scenario_code:
        stmt = stmt.where(TravelScenario.code == scenario_code)
    if snapshot_id:
        stmt = stmt.where(ScenarioRun.market_snapshot_id == uuid.UUID(snapshot_id))
    if run_status:
        stmt = stmt.where(ScenarioRun.status == run_status.value)
    if run_type:
        stmt = stmt.where(ScenarioRun.run_type == run_type.value)
    if confidence_level:
        stmt = stmt.where(ScenarioRun.confidence_level == confidence_level.value)
    if origin:
        stmt = stmt.where(
            TravelScenario.origin_city_id
            == select(City.id).where(City.code == origin).scalar_subquery()
        )
    if destination:
        stmt = stmt.where(
            TravelScenario.destination_city_id
            == select(City.id).where(City.code == destination).scalar_subquery()
        )
    if transport_type:
        stmt = stmt.where(TravelScenario.transport_type == transport_type.value)
    if observation_date_from:
        stmt = stmt.where(ScenarioRun.observation_date >= observation_date_from)
    if observation_date_to:
        stmt = stmt.where(ScenarioRun.observation_date <= observation_date_to)
    if profile_version:
        stmt = stmt.where(ScenarioRun.profile_version == profile_version)
    if min_quality_score is not None:
        stmt = stmt.where(ScenarioRun.quality_score >= min_quality_score)
    if complete_only:
        stmt = stmt.where(ScenarioRun.total_estimated_cost.is_not(None))
    if not include_synthetic:
        stmt = stmt.where(ScenarioRun.contains_synthetic_data.is_(False))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(ScenarioRun.started_at.desc()).offset(page.offset).limit(page.limit)
    ).all()

    return {
        "items": [run_brief(row) for row in rows],
        "meta": {
            "page": page.page,
            "page_size": page.page_size,
            "total": total,
            "total_pages": (total + page.page_size - 1) // page.page_size,
        },
        "metric_title": METRIC_TITLE_RU,
    }


@router.get("/{run_id}", summary="Расчет по идентификатору")
def get_run(run_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    run = get_or_404(session, ScenarioRun, run_id, "Расчет")
    payload = run_full(run)
    payload["scenario_detail"] = scenario_full(run.scenario) if run.scenario else None
    if run.market_snapshot_id:
        snapshot = session.get(MarketSnapshot, run.market_snapshot_id)
        if snapshot is not None:
            payload["snapshot"] = snapshot_brief(snapshot)
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload


@router.get("/{run_id}/explain", summary="Объяснение результата")
def explain(run_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    """Полная объяснимость расчета.

    Показывает использованные источники, число полученных и исключенных
    предложений, медиану каждого источника, межисточниковое расхождение,
    примененный профиль и причины снижения Quality Score.
    """
    run = get_or_404(session, ScenarioRun, run_id, "Расчет")
    return {
        "run_id": str(run.id),
        "scenario_code": run.scenario.code if run.scenario else None,
        "status": run.status,
        "component_statuses": list(run.component_statuses or []),
        "explainability": run.explainability_payload,
        "quality": {"score": run.quality_score, "breakdown": run.quality_breakdown},
        "confidence": {
            "level": run.confidence_level,
            "reason": run.confidence_reason,
            "factors": run.confidence_factors,
        },
        "versions": {
            "profile": f"{run.profile_code}@{run.profile_version}",
            "normalization": run.normalization_version,
            "engine": run.engine_version,
        },
        "error_summary": list(run.error_summary or []),
        "metric_title": METRIC_TITLE_RU,
        "disclaimer": METRIC_DISCLAIMER_RU,
    }


@router.get("/{run_id}/source-breakdown", summary="Разбор по источникам")
def source_breakdown(run_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    """Вклад каждого источника и причины недопуска.

    Источник, не прошедший eligibility, показывается с перечнем причин —
    результат не должен выглядеть как «источник просто отсутствовал».
    """
    run = get_or_404(session, ScenarioRun, run_id, "Расчет")
    return {
        "run_id": str(run.id),
        "scenario_code": run.scenario.code if run.scenario else None,
        **(run.source_breakdown or {}),
        "source_count": run.source_count,
        "source_codes": list(run.source_codes or []),
        "disagreement": {
            "transport": run.transport_disagreement,
            "accommodation": run.hotel_disagreement,
        },
    }
