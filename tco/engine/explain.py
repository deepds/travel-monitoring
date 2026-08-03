"""Формирование explainability payload (SCOPE-R P §17).

Каждый ScenarioRun обязан объяснять: какие источники использованы, сколько
предложений получено, сколько исключено и почему, медиану каждого источника,
межисточниковое расхождение, примененный профиль и версии, статус полноты
и причины снижения Quality Score.
"""

from __future__ import annotations

from typing import Any

from tco.core.enums import ComponentType, RunStatus
from tco.engine.aggregation import ComponentAggregate
from tco.engine.confidence import ScenarioConfidence
from tco.engine.quality import QualityScore
from tco.engine.selection import OutlierReport, SelectionStats
from tco.version import METRIC_DISCLAIMER_RU, METRIC_TITLE_RU, version_payload

#: Человекочитаемые причины исключения предложений.
EXCLUSION_TITLES: dict[str, str] = {
    "INVALID": "Невалидное предложение",
    "TECHNICAL_DUPLICATE": "Технический дубликат",
    "PROFILE_FILTER": "Не соответствует профилю расчета",
    "OUTLIER": "Выброс (IQR)",
    "SOURCE_NOT_ELIGIBLE": "Источник не допущен к расчету",
}

FILTER_REASON_TITLES: dict[str, str] = {
    "TRANSPORT_TYPE_MISMATCH": "Вид транспорта не совпадает со сценарием",
    "BAGGAGE_UNCLASSIFIED": "Багаж не классифицирован — тариф с багажом недоступен",
    "BAGGAGE_MISMATCH": "Багаж не соответствует выбранному авиатарифу",
    "TOO_MANY_STOPS": "Слишком много пересадок",
    "RAIL_CLASS_UNCLASSIFIED": "Класс вагона не определен",
    "RAIL_CLASS_MISMATCH": "Класс вагона не совпадает со сценарием",
    "ACCOMMODATION_TYPE_MISMATCH": "Тип размещения не совпадает со сценарием",
    "STARS_MISMATCH": "Звездность не совпадает со сценарием",
    "MEAL_MISMATCH": "Питание не соответствует запрошенному",
    "CANCELLATION_MISMATCH": "Условие отмены не соответствует запрошенному",
    "CAPACITY_UNCONFIRMED": "Вместимость для состава туристов не подтверждена",
    "MISSING_FLIGHT_DETAIL": "Нет детальных данных авиапредложения",
    "MISSING_RAIL_DETAIL": "Нет детальных данных ЖД-предложения",
    "MISSING_ACCOMMODATION_DETAIL": "Нет детальных данных по размещению",
}


def build_explainability(
    *,
    scenario_params: dict[str, Any],
    profile: dict[str, Any],
    status: RunStatus,
    component_statuses: list[str],
    transport: ComponentAggregate,
    accommodation: ComponentAggregate,
    quality: QualityScore,
    confidence: ScenarioConfidence,
    selection: dict[str, SelectionStats],
    outliers: dict[str, OutlierReport],
    equivalence_groups: dict[str, int],
    snapshot: dict[str, Any],
    totals: dict[str, Any],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Собирает полный объяснительный payload расчета."""
    return {
        "metric": {
            "title": METRIC_TITLE_RU,
            "disclaimer": METRIC_DISCLAIMER_RU,
        },
        "versions": version_payload(),
        "profile": profile,
        "scenario": scenario_params,
        "snapshot": snapshot,
        "status": {
            "run_status": status.value,
            "component_statuses": component_statuses,
            "total_available": totals.get("total_estimated_cost") is not None,
            "explanation": _status_explanation(status, component_statuses),
        },
        "components": {
            "transport": transport.as_dict(),
            "accommodation": accommodation.as_dict(),
        },
        "totals": totals,
        "quality": quality.as_dict(),
        "confidence": confidence.as_dict(),
        "selection": {
            component: {
                **stats.as_dict(),
                "filter_reason_titles": {
                    reason: FILTER_REASON_TITLES.get(reason, reason)
                    for reason in stats.filter_reasons
                },
            }
            for component, stats in selection.items()
        },
        "outliers": {component: report.as_dict() for component, report in outliers.items()},
        "cross_source_equivalence": equivalence_groups,
        "exclusion_titles": EXCLUSION_TITLES,
        "errors": errors,
    }


def build_source_breakdown(
    transport: ComponentAggregate, accommodation: ComponentAggregate
) -> dict[str, Any]:
    """Разрез по источникам — ``GET /scenario-runs/{id}/source-breakdown``."""
    rows: list[dict[str, Any]] = []
    for component in (transport, accommodation):
        for source in component.source_aggregates:
            row = source.as_dict()
            row["component_title"] = _component_title(component.component)
            row["is_used_in_result"] = source.source_code in component.eligible_source_codes
            rows.append(row)
    return {
        "rows": rows,
        "summary": {
            "transport_sources": transport.eligible_source_codes,
            "accommodation_sources": accommodation.eligible_source_codes,
            "transport_disagreement": transport.disagreement,
            "accommodation_disagreement": accommodation.disagreement,
        },
    }


def _status_explanation(status: RunStatus, component_statuses: list[str]) -> str:
    if status == RunStatus.SUCCESS:
        base = "Оба компонента рассчитаны, итоговая стоимость определена."
    elif status == RunStatus.PARTIAL_SUCCESS:
        missing = []
        if "PARTIAL_TRANSPORT_MISSING" in component_statuses:
            missing.append("транспорт")
        if "PARTIAL_HOTEL_MISSING" in component_statuses:
            missing.append("проживание")
        base = (
            f"Компонент не рассчитан: {', '.join(missing)}. "
            "Итоговая полная стоимость не определяется, старое значение не подставляется."
            if missing
            else "Расчет выполнен частично."
        )
    elif status == RunStatus.NO_DATA:
        base = "Пригодных предложений не получено ни по одному компоненту."
    elif status == RunStatus.FAILED:
        base = "Сбор данных завершился ошибкой по всем источникам."
    else:
        base = "Сценарий не поддерживается — внешние запросы не выполнялись."

    if "COMPLETE_SINGLE_SOURCE" in component_statuses:
        base += " Как минимум один компонент рассчитан по единственному источнику."
    return base


def _component_title(component: ComponentType) -> str:
    return "Транспорт" if component == ComponentType.TRANSPORT else "Проживание"
