"""Агрегаты для дашборда.

Все запросы работают по последним ``ScenarioRun`` в срезе наблюдения:
по умолчанию дашборд показывает последний актуальный снимок (дополнение
DELTA к разделу 2), исторический анализ доступен по всем внутрисуточным
наблюдениям либо по дневному представлению.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from tco.core.enums import RunStatus, RunType, TransportType
from tco.core.utils import round_display, to_decimal, utcnow
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.engine.statistics import median, pct_change
from tco.version import METRIC_DISCLAIMER_RU, METRIC_TITLE_RU


@dataclass(slots=True)
class DashboardFilters:
    """Фильтры дашборда."""

    origin_city_code: str | None = None
    destination_city_code: str | None = None
    transport_type: TransportType | None = None
    accommodation_type: str | None = None
    stars: str | None = None
    scenario_codes: Sequence[str] = ()
    date_from: date | None = None
    date_to: date | None = None
    profile_version: str | None = None
    include_synthetic: bool = False
    #: Только расчеты с итоговой стоимостью (оба компонента рассчитаны).
    complete_only: bool = False
    min_quality_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin_city_code": self.origin_city_code,
            "destination_city_code": self.destination_city_code,
            "transport_type": self.transport_type.value if self.transport_type else None,
            "accommodation_type": self.accommodation_type,
            "stars": self.stars,
            "scenario_codes": list(self.scenario_codes),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "profile_version": self.profile_version,
            "include_synthetic": self.include_synthetic,
            "complete_only": self.complete_only,
            "min_quality_score": self.min_quality_score,
        }


def _apply_filters(stmt: Select, filters: DashboardFilters) -> Select:
    stmt = stmt.join(TravelScenario, ScenarioRun.scenario_id == TravelScenario.id)
    conditions = [TravelScenario.deleted_at.is_(None)]

    if filters.origin_city_code:
        origin = select(City.id).where(City.code == filters.origin_city_code).scalar_subquery()
        conditions.append(TravelScenario.origin_city_id == origin)
    if filters.destination_city_code:
        destination = (
            select(City.id).where(City.code == filters.destination_city_code).scalar_subquery()
        )
        conditions.append(TravelScenario.destination_city_id == destination)
    if filters.transport_type:
        conditions.append(TravelScenario.transport_type == filters.transport_type.value)
    if filters.accommodation_type:
        conditions.append(TravelScenario.accommodation_type == filters.accommodation_type)
    if filters.stars:
        conditions.append(TravelScenario.stars == filters.stars)
    if filters.scenario_codes:
        conditions.append(TravelScenario.code.in_(list(filters.scenario_codes)))
    if filters.date_from:
        conditions.append(ScenarioRun.observation_date >= filters.date_from)
    if filters.date_to:
        conditions.append(ScenarioRun.observation_date <= filters.date_to)
    if filters.profile_version:
        conditions.append(ScenarioRun.profile_version == filters.profile_version)
    if not filters.include_synthetic:
        conditions.append(ScenarioRun.contains_synthetic_data.is_(False))
    if filters.complete_only:
        conditions.append(ScenarioRun.total_estimated_cost.is_not(None))
    if filters.min_quality_score is not None:
        conditions.append(ScenarioRun.quality_score >= filters.min_quality_score)

    return stmt.where(and_(*conditions))


def latest_runs(
    session: Session,
    filters: DashboardFilters | None = None,
    *,
    as_of: date | None = None,
    run_types: Sequence[RunType] = (RunType.MONITORING,),
) -> list[ScenarioRun]:
    """Последний расчет каждого сценария на момент ``as_of``.

    При нескольких внутрисуточных снимках берется самый свежий.
    """
    filters = filters or DashboardFilters()
    stmt = select(ScenarioRun)
    if run_types:
        stmt = stmt.where(ScenarioRun.run_type.in_([item.value for item in run_types]))
    if as_of:
        stmt = stmt.where(ScenarioRun.observation_date <= as_of)
    stmt = _apply_filters(stmt, filters).order_by(
        ScenarioRun.scenario_id, ScenarioRun.started_at.desc()
    )

    seen: set[Any] = set()
    result: list[ScenarioRun] = []
    for run in session.scalars(stmt).all():
        if run.scenario_id in seen:
            continue
        seen.add(run.scenario_id)
        result.append(run)
    return result


def _median_total(runs: Iterable[ScenarioRun]) -> float | None:
    values = [to_decimal(run.total_estimated_cost) for run in runs]
    return median([value for value in values if value is not None])


def overview(
    session: Session, filters: DashboardFilters | None = None
) -> dict[str, Any]:
    """Главные показатели дашборда (SCOPE-R P §15)."""
    filters = filters or DashboardFilters()
    today = utcnow().date()

    current = latest_runs(session, filters)
    # Суточное окно нужно на старте наблюдения: недельная и месячная динамика
    # появляются только после накопления истории.
    day_ago = latest_runs(session, filters, as_of=today - timedelta(days=1))
    week_ago = latest_runs(session, filters, as_of=today - timedelta(days=7))
    month_ago = latest_runs(session, filters, as_of=today - timedelta(days=30))

    complete = [run for run in current if run.total_estimated_cost is not None]
    current_median = _median_total(complete)

    # Сравнение идет по одному и тому же множеству сценариев: иначе изменение
    # отражало бы смену состава, а не динамику рынка.
    comparable = {run.scenario_id for run in complete}
    day_median = _median_total(
        [run for run in day_ago if run.scenario_id in comparable and run.total_estimated_cost]
    )
    week_median = _median_total(
        [run for run in week_ago if run.scenario_id in comparable and run.total_estimated_cost]
    )
    month_median = _median_total(
        [run for run in month_ago if run.scenario_id in comparable and run.total_estimated_cost]
    )

    shares = [run.transport_share for run in complete if run.transport_share is not None]
    quality = [run.quality_score for run in current if run.quality_score is not None]
    last_update = max((run.completed_at for run in current if run.completed_at), default=None)

    status_counts = _status_counts(current)
    return {
        "metric_title": METRIC_TITLE_RU,
        "disclaimer": METRIC_DISCLAIMER_RU,
        "scenario_count": len(current),
        "complete_count": len(complete),
        "median_total_cost": round_display(current_median, 0),
        "change_1d": pct_change(current_median, day_median),
        "change_7d": pct_change(current_median, week_median),
        "change_30d": pct_change(current_median, month_median),
        "comparable_scenarios_1d": sum(
            1 for run in day_ago if run.scenario_id in comparable and run.total_estimated_cost
        ),
        "comparable_scenarios_7d": sum(
            1 for run in week_ago if run.scenario_id in comparable and run.total_estimated_cost
        ),
        "comparable_scenarios_30d": sum(
            1 for run in month_ago if run.scenario_id in comparable and run.total_estimated_cost
        ),
        "median_transport_share": round(sum(shares) / len(shares), 4) if shares else None,
        "avg_quality_score": round(sum(quality) / len(quality), 1) if quality else None,
        "status_counts": status_counts,
        "success_rate": _rate(status_counts, RunStatus.SUCCESS.value, len(current)),
        "partial_rate": _rate(status_counts, RunStatus.PARTIAL_SUCCESS.value, len(current)),
        "complete_rate": round(len(complete) / len(current), 4) if current else None,
        "kpi_pass_rate": _kpi_pass_rate(current),
        "last_update": last_update.isoformat() if last_update else None,
        "contains_synthetic": any(run.contains_synthetic_data for run in current),
        "filters": filters.as_dict(),
    }


def _status_counts(runs: Sequence[ScenarioRun]) -> dict[str, int]:
    counts: dict[str, int] = {status.value: 0 for status in RunStatus}
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
    return counts


def _rate(counts: dict[str, int], key: str, total: int) -> float | None:
    return round(counts.get(key, 0) / total, 4) if total else None


def _kpi_pass_rate(runs: Sequence[ScenarioRun]) -> float | None:
    """Доля расчетов, засчитываемых в KPI стабильности (SCOPE-R O §2)."""
    if not runs:
        return None
    return round(sum(1 for run in runs if run.counts_toward_kpi) / len(runs), 4)


def directions(
    session: Session, filters: DashboardFilters | None = None
) -> list[dict[str, Any]]:
    """Сравнительная таблица направлений."""
    filters = filters or DashboardFilters()
    today = utcnow().date()
    current = latest_runs(session, filters)
    day_ago = {
        run.scenario_id: run for run in latest_runs(session, filters, as_of=today - timedelta(days=1))
    }
    week_ago = {
        run.scenario_id: run for run in latest_runs(session, filters, as_of=today - timedelta(days=7))
    }
    month_ago = {
        run.scenario_id: run
        for run in latest_runs(session, filters, as_of=today - timedelta(days=30))
    }

    grouped: dict[tuple[str, str, str], list[ScenarioRun]] = {}
    for run in current:
        scenario = run.scenario
        key = (
            scenario.origin_city.code,
            scenario.destination_city.code,
            scenario.transport_type,
        )
        grouped.setdefault(key, []).append(run)

    rows: list[dict[str, Any]] = []
    for (origin, destination, transport), runs in sorted(grouped.items()):
        complete = [run for run in runs if run.total_estimated_cost is not None]
        current_median = _median_total(complete)
        day_median = _median_total(
            [day_ago[run.scenario_id] for run in complete if run.scenario_id in day_ago]
        )
        week_median = _median_total(
            [week_ago[run.scenario_id] for run in complete if run.scenario_id in week_ago]
        )
        month_median = _median_total(
            [month_ago[run.scenario_id] for run in complete if run.scenario_id in month_ago]
        )
        sample = runs[0].scenario

        rows.append(
            {
                "origin_city_code": origin,
                "origin_city_name": sample.origin_city.name,
                "destination_city_code": destination,
                "destination_city_name": sample.destination_city.name,
                "transport_type": transport,
                "scenario_count": len(runs),
                "complete_count": len(complete),
                "median_total_cost": round_display(current_median, 0),
                "median_transport": round_display(
                    median([to_decimal(r.transport_median) for r in runs if r.transport_median]), 0
                ),
                "median_hotel": round_display(
                    median([to_decimal(r.hotel_median) for r in runs if r.hotel_median]), 0
                ),
                "p25": round_display(
                    median([to_decimal(r.total_p25) for r in complete if r.total_p25]), 0
                ),
                "p75": round_display(
                    median([to_decimal(r.total_p75) for r in complete if r.total_p75]), 0
                ),
                "change_1d": pct_change(current_median, day_median),
                "change_7d": pct_change(current_median, week_median),
                "change_30d": pct_change(current_median, month_median),
                "avg_quality_score": _avg(
                    [run.quality_score for run in runs if run.quality_score is not None]
                ),
                "confidence_levels": _count_by(runs, "confidence_level"),
                "last_update": max(
                    (run.completed_at.isoformat() for run in runs if run.completed_at),
                    default=None,
                ),
                "contains_synthetic": any(run.contains_synthetic_data for run in runs),
            }
        )
    return rows


def trends(
    session: Session,
    filters: DashboardFilters | None = None,
    *,
    days: int = 30,
    granularity: str = "DAY",
) -> dict[str, Any]:
    """Динамика стоимости и компонентов.

    ``granularity=DAY`` агрегирует внутрисуточные снимки в дневную точку,
    ``SNAPSHOT`` показывает каждое наблюдение отдельно.
    """
    filters = filters or DashboardFilters()
    since = utcnow().date() - timedelta(days=days)
    stmt = select(ScenarioRun).where(ScenarioRun.observation_date >= since)
    stmt = _apply_filters(stmt, filters).order_by(ScenarioRun.observation_date)
    runs = session.scalars(stmt).all()

    buckets: dict[str, list[ScenarioRun]] = {}
    for run in runs:
        if granularity == "SNAPSHOT" and run.started_at:
            key = run.started_at.strftime("%Y-%m-%dT%H:00")
        else:
            key = run.observation_date.isoformat()
        buckets.setdefault(key, []).append(run)

    points = []
    for key in sorted(buckets):
        bucket = buckets[key]
        complete = [run for run in bucket if run.total_estimated_cost is not None]
        points.append(
            {
                "period": key,
                "scenario_count": len({run.scenario_id for run in bucket}),
                "run_count": len(bucket),
                "median_total": round_display(_median_total(complete), 0),
                "median_transport": round_display(
                    median([to_decimal(r.transport_median) for r in bucket if r.transport_median]), 0
                ),
                "median_hotel": round_display(
                    median([to_decimal(r.hotel_median) for r in bucket if r.hotel_median]), 0
                ),
                "p25": round_display(
                    median([to_decimal(r.total_p25) for r in complete if r.total_p25]), 0
                ),
                "p75": round_display(
                    median([to_decimal(r.total_p75) for r in complete if r.total_p75]), 0
                ),
                "avg_quality_score": _avg(
                    [run.quality_score for run in bucket if run.quality_score is not None]
                ),
                "complete_rate": round(len(complete) / len(bucket), 4) if bucket else None,
            }
        )

    return {"granularity": granularity, "days": days, "points": points, "filters": filters.as_dict()}


def cost_structure(
    session: Session, filters: DashboardFilters | None = None
) -> dict[str, Any]:
    """Структура стоимости: транспорт против проживания."""
    filters = filters or DashboardFilters()
    runs = [run for run in latest_runs(session, filters) if run.total_estimated_cost is not None]
    if not runs:
        return {"components": [], "scenario_count": 0, "filters": filters.as_dict()}

    transport = median([to_decimal(run.transport_median) for run in runs if run.transport_median])
    hotel = median([to_decimal(run.hotel_median) for run in runs if run.hotel_median])
    total = (transport or 0) + (hotel or 0)

    return {
        "scenario_count": len(runs),
        "median_total": round_display(total, 0),
        "components": [
            {
                "component": "TRANSPORT",
                "title": "Транспорт",
                "median": round_display(transport, 0),
                "share": round(transport / total, 4) if total and transport else None,
            },
            {
                "component": "ACCOMMODATION",
                "title": "Проживание",
                "median": round_display(hotel, 0),
                "share": round(hotel / total, 4) if total and hotel else None,
            },
        ],
        "note": (
            "Сумма компонентных медиан не является медианой всех возможных "
            "комбинаций транспорта и проживания."
        ),
        "filters": filters.as_dict(),
    }


def significant_changes(
    session: Session,
    filters: DashboardFilters | None = None,
    *,
    window_days: int = 7,
    threshold: float = 0.05,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Направления с существенным изменением стоимости."""
    filters = filters or DashboardFilters()
    today = utcnow().date()
    current = latest_runs(session, filters)
    previous = {
        run.scenario_id: run
        for run in latest_runs(session, filters, as_of=today - timedelta(days=window_days))
    }

    rows: list[dict[str, Any]] = []
    for run in current:
        if run.total_estimated_cost is None:
            continue
        base = previous.get(run.scenario_id)
        if base is None or base.total_estimated_cost is None or base.id == run.id:
            continue
        change = pct_change(run.total_estimated_cost, base.total_estimated_cost)
        if change is None or abs(change) < threshold:
            continue
        scenario = run.scenario
        rows.append(
            {
                "scenario_id": str(scenario.id),
                "scenario_code": scenario.code,
                "scenario_name": scenario.name,
                "origin": scenario.origin_city.name,
                "destination": scenario.destination_city.name,
                "transport_type": scenario.transport_type,
                "current_total": round_display(run.total_estimated_cost, 0),
                "previous_total": round_display(base.total_estimated_cost, 0),
                "change": round(change, 4),
                "direction": "UP" if change > 0 else "DOWN",
                "observation_date": run.observation_date.isoformat(),
                "quality_score": run.quality_score,
                "confidence_level": run.confidence_level,
            }
        )

    rows.sort(key=lambda item: abs(item["change"]), reverse=True)
    return rows[:limit]


def quality_overview(
    session: Session, filters: DashboardFilters | None = None, *, window_days: int = 30
) -> dict[str, Any]:
    """Экран качества: статусы, Quality Score, уверенность, источники."""
    from tco.services.source_metrics import source_overview

    filters = filters or DashboardFilters()
    since = utcnow().date() - timedelta(days=window_days)
    stmt = select(ScenarioRun).where(ScenarioRun.observation_date >= since)
    stmt = _apply_filters(stmt, filters)
    runs = session.scalars(stmt).all()

    counts = _status_counts(runs)
    quality = [run.quality_score for run in runs if run.quality_score is not None]
    return {
        "window_days": window_days,
        "run_count": len(runs),
        "status_counts": counts,
        "success_rate": _rate(counts, RunStatus.SUCCESS.value, len(runs)),
        "partial_rate": _rate(counts, RunStatus.PARTIAL_SUCCESS.value, len(runs)),
        "failed_rate": _rate(counts, RunStatus.FAILED.value, len(runs)),
        "no_data_rate": _rate(counts, RunStatus.NO_DATA.value, len(runs)),
        "kpi_pass_rate": _kpi_pass_rate(runs),
        "complete_rate": round(
            sum(1 for run in runs if run.total_estimated_cost is not None) / len(runs), 4
        )
        if runs
        else None,
        "avg_quality_score": _avg(quality),
        "median_quality_score": round(median(quality), 1) if quality else None,
        "confidence_levels": _count_by(runs, "confidence_level"),
        "single_source_runs": sum(
            1 for run in runs if "COMPLETE_SINGLE_SOURCE" in (run.component_statuses or [])
        ),
        "sources": source_overview(session, window_days=window_days),
        "filters": filters.as_dict(),
    }


def kpi_summary(session: Session, *, window_days: int = 7) -> dict[str, Any]:
    """Сводка KPI стабильности для мониторинга и отчетности."""
    since = utcnow().date() - timedelta(days=window_days)
    runs = session.scalars(
        select(ScenarioRun)
        .where(ScenarioRun.observation_date >= since)
        .where(ScenarioRun.run_type == RunType.MONITORING.value)
    ).all()
    counts = _status_counts(runs)
    return {
        "window_days": window_days,
        "run_count": len(runs),
        "status_counts": counts,
        "success_rate": _rate(counts, RunStatus.SUCCESS.value, len(runs)),
        "partial_rate": _rate(counts, RunStatus.PARTIAL_SUCCESS.value, len(runs)),
        "kpi_pass_rate": _kpi_pass_rate(runs),
        "avg_quality_score": _avg(
            [run.quality_score for run in runs if run.quality_score is not None]
        ),
        "distinct_scenarios": len({run.scenario_id for run in runs}),
        "calculated_at": utcnow().isoformat(),
    }


def scenario_history(
    session: Session, scenario_id: Any, *, limit: int = 120
) -> list[dict[str, Any]]:
    """История расчетов одного сценария — экран анализа направления."""
    runs = session.scalars(
        select(ScenarioRun)
        .where(ScenarioRun.scenario_id == scenario_id)
        .order_by(ScenarioRun.started_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "run_id": str(run.id),
            "observation_date": run.observation_date.isoformat(),
            "started_at": run.started_at.isoformat(),
            "run_type": run.run_type,
            "status": run.status,
            "component_statuses": list(run.component_statuses or []),
            "transport_median": round_display(run.transport_median, 0),
            "hotel_median": round_display(run.hotel_median, 0),
            "total_estimated_cost": round_display(run.total_estimated_cost, 0),
            "total_p25": round_display(run.total_p25, 0),
            "total_p75": round_display(run.total_p75, 0),
            "price_per_person": round_display(run.price_per_person, 0),
            "quality_score": run.quality_score,
            "confidence_level": run.confidence_level,
            "lead_time_days": run.lead_time_days,
            "source_count": run.source_count,
            "source_codes": list(run.source_codes or []),
            "valid_offer_count": run.valid_offer_count,
            "excluded_offer_count": run.excluded_offer_count,
            "outlier_offer_count": run.outlier_offer_count,
            "profile_version": run.profile_version,
            "contains_synthetic_data": run.contains_synthetic_data,
        }
        for run in reversed(runs)
    ]


def _avg(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _count_by(runs: Sequence[ScenarioRun], attribute: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        value = getattr(run, attribute, None)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
