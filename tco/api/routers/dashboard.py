"""Управленческий дашборд (DELTA §6.10).

По умолчанию показывается последний актуальный Market Snapshot; исторический
анализ доступен как по внутрисуточным снимкам, так и по дневному агрегату.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from tco.api.deps import SessionDep, ViewerDep, get_or_404
from tco.core.enums import TransportType
from tco.core.logging import get_logger
from tco.db.models.scenario import TravelScenario
from tco.services import dashboard as service
from tco.services.dashboard import DashboardFilters
from tco.version import METRIC_DISCLAIMER_RU, METRIC_TITLE_RU

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def dashboard_filters(
    origin: Annotated[str | None, Query(description="Код города отправления")] = None,
    destination: Annotated[str | None, Query(description="Код города назначения")] = None,
    transport_type: TransportType | None = None,
    accommodation_type: str | None = None,
    stars: str | None = None,
    scenario_codes: Annotated[list[str] | None, Query(description="Коды сценариев")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    profile_version: str | None = None,
    include_synthetic: Annotated[
        bool, Query(description="Включать расчеты с синтетическими данными")
    ] = False,
    complete_only: Annotated[
        bool, Query(description="Только расчеты с итоговой стоимостью")
    ] = False,
    min_quality_score: Annotated[float | None, Query(ge=0, le=100)] = None,
) -> DashboardFilters:
    return DashboardFilters(
        origin_city_code=origin,
        destination_city_code=destination,
        transport_type=transport_type,
        accommodation_type=accommodation_type,
        stars=stars,
        scenario_codes=tuple(scenario_codes or ()),
        date_from=date_from,
        date_to=date_to,
        profile_version=profile_version,
        include_synthetic=include_synthetic,
        complete_only=complete_only,
        min_quality_score=min_quality_score,
    )


FiltersDep = Annotated[DashboardFilters, Depends(dashboard_filters)]


@router.get("/overview", summary="Главные показатели")
def overview(session: SessionDep, _: ViewerDep, filters: FiltersDep) -> dict[str, Any]:
    """Расчетная типовая стоимость, изменения за 7 и 30 дней, качество."""
    payload = service.overview(session, filters)
    payload["metric_title"] = METRIC_TITLE_RU
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload


@router.get("/directions", summary="Сравнительная таблица направлений")
def directions(session: SessionDep, _: ViewerDep, filters: FiltersDep) -> dict[str, Any]:
    items = service.directions(session, filters)
    return {"items": items, "total": len(items), "filters": filters.as_dict()}


@router.get("/trends", summary="Динамика стоимости")
def trends(
    session: SessionDep,
    _: ViewerDep,
    filters: FiltersDep,
    days: Annotated[int, Query(ge=1, le=365, description="Глубина истории в днях")] = 30,
    granularity: Annotated[
        str,
        Query(
            pattern="^(DAY|SNAPSHOT)$",
            description="DAY — дневной агрегат, SNAPSHOT — каждое наблюдение",
        ),
    ] = "DAY",
) -> dict[str, Any]:
    return service.trends(session, filters, days=days, granularity=granularity)


@router.get("/cost-structure", summary="Структура стоимости")
def cost_structure(session: SessionDep, _: ViewerDep, filters: FiltersDep) -> dict[str, Any]:
    return service.cost_structure(session, filters)


@router.get("/changes", summary="Направления с существенными изменениями")
def changes(
    session: SessionDep,
    _: ViewerDep,
    filters: FiltersDep,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
    threshold: Annotated[float, Query(ge=0, le=1, description="Порог изменения, доля")] = 0.05,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = service.significant_changes(
        session, filters, window_days=window_days, threshold=threshold, limit=limit
    )
    return {
        "items": items,
        "total": len(items),
        "window_days": window_days,
        "threshold": threshold,
    }


@router.get("/quality", summary="Качество данных и источников")
def quality(
    session: SessionDep,
    _: ViewerDep,
    filters: FiltersDep,
    window_days: Annotated[int, Query(ge=1, le=180)] = 30,
) -> dict[str, Any]:
    return service.quality_overview(session, filters, window_days=window_days)


@router.get("/kpi", summary="Сводка KPI стабильности")
def kpi(
    session: SessionDep,
    _: ViewerDep,
    window_days: Annotated[int, Query(ge=1, le=180)] = 7,
) -> dict[str, Any]:
    """Доли SUCCESS и PARTIAL_SUCCESS показываются раздельно (SCOPE-R O §2)."""
    return service.kpi_summary(session, window_days=window_days)


@router.get("/scenarios/{scenario_id}/history", summary="История расчетов сценария")
def scenario_history(
    scenario_id: str,
    session: SessionDep,
    _: ViewerDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 120,
) -> dict[str, Any]:
    """Экран анализа направления: динамика по конкретному сценарию."""
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    items = service.scenario_history(session, scenario.id, limit=limit)
    return {
        "scenario": {
            "id": str(scenario.id),
            "code": scenario.code,
            "name": scenario.name,
        },
        "items": items,
        "total": len(items),
    }
