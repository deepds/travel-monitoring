"""Расчет Source Confidence и агрегированных метрик источников."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tco.core.enums import ConnectorOutcome
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.run import ScenarioRun
from tco.db.models.source import Source, SourceConfidence, SourceMetric
from tco.engine.confidence import SourceConfidenceResult, calculate_source_confidence
from tco.version import CONFIDENCE_FORMULA_VERSION

logger = get_logger(__name__)

#: Юридическая надежность по статусу источника (фактор 10%).
LEGAL_RELIABILITY: dict[str, float] = {
    "APPROVED": 1.0,
    "CONDITIONAL": 0.7,
    "CANDIDATE": 0.4,
    "REJECTED": 0.0,
}


def source_metric_summary(
    session: Session, source_id: Any, *, window_days: int = 30
) -> dict[str, Any]:
    """Сводка технических метрик источника за окно наблюдения."""
    since = utcnow() - timedelta(days=window_days)
    rows = session.scalars(
        select(SourceMetric)
        .where(SourceMetric.source_id == source_id)
        .where(SourceMetric.observed_at >= since)
    ).all()

    if not rows:
        return {
            "window_days": window_days,
            "call_count": 0,
            "success_rate": None,
            "avg_latency_ms": None,
            "valid_offer_ratio": None,
            "field_completeness": None,
            "schema_error_rate": None,
            "total_offers": 0,
        }

    successes = sum(1 for row in rows if ConnectorOutcome(row.outcome).is_ok)
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    normalized = sum(row.normalized_offer_count for row in rows)
    valid = sum(row.valid_offer_count for row in rows)
    completeness = [
        row.required_field_completeness
        for row in rows
        if row.required_field_completeness is not None
    ]
    schema_errors = sum(
        1 for row in rows if row.outcome == ConnectorOutcome.SCHEMA_ERROR.value
    )

    return {
        "window_days": window_days,
        "call_count": len(rows),
        "success_rate": successes / len(rows),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "valid_offer_ratio": (valid / normalized) if normalized else None,
        "field_completeness": (sum(completeness) / len(completeness)) if completeness else None,
        "schema_error_rate": schema_errors / len(rows),
        "total_offers": normalized,
        "error_breakdown": _error_breakdown(rows),
    }


def _error_breakdown(rows: list[SourceMetric]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for row in rows:
        if not ConnectorOutcome(row.outcome).is_ok:
            breakdown[row.outcome] = breakdown.get(row.outcome, 0) + 1
    return dict(sorted(breakdown.items()))


def cross_source_agreement(
    session: Session, source_code: str, *, window_days: int = 30
) -> float | None:
    """Согласованность источника с остальными.

    Берутся расчеты за окно, где источник участвовал вместе с другими,
    и оценивается, насколько мало было межисточниковое расхождение.
    ``None`` — источник не с чем было сравнить.
    """
    since = utcnow() - timedelta(days=window_days)
    runs = session.scalars(
        select(ScenarioRun).where(ScenarioRun.created_at >= since)
    ).all()

    values: list[float] = []
    for run in runs:
        if source_code not in (run.source_codes or []):
            continue
        for disagreement, source_count in (
            (run.transport_disagreement, run.transport_source_count),
            (run.hotel_disagreement, run.hotel_source_count),
        ):
            if disagreement is not None and source_count >= 2:
                # Расхождение 0 → согласованность 1; 30% и выше → 0.
                values.append(max(0.0, 1.0 - min(disagreement / 0.30, 1.0)))
    if not values:
        return None
    return sum(values) / len(values)


def compute_source_confidence(
    session: Session,
    source: Source,
    *,
    window_days: int = 30,
    calculation_date: date | None = None,
) -> SourceConfidenceResult:
    """Считает Source Confidence одного источника."""
    summary = source_metric_summary(session, source.id, window_days=window_days)
    agreement = cross_source_agreement(session, source.code, window_days=window_days)
    schema_stability = (
        None if summary["schema_error_rate"] is None else 1.0 - summary["schema_error_rate"]
    )

    # Результаты ручной проверки поступают из challenge set; до их появления
    # фактор остается неизвестным и заменяется нейтральным значением.
    manual_review = (source.config or {}).get("manual_review_score")

    return calculate_source_confidence(
        source_code=source.code,
        success_rate=summary["success_rate"],
        field_completeness=summary["field_completeness"],
        cross_source_agreement=agreement,
        valid_offer_ratio=summary["valid_offer_ratio"],
        schema_stability=schema_stability,
        legal_reliability=LEGAL_RELIABILITY.get(source.qualification_status, 0.4),
        manual_review=float(manual_review) if manual_review is not None else None,
        input_metrics={**summary, "cross_source_agreement": agreement},
        calculation_date=calculation_date or utcnow().date(),
    )


def persist_source_confidence(
    session: Session, source: Source, result: SourceConfidenceResult
) -> SourceConfidence:
    """Сохраняет расчет, сохраняя ручной override, если он был задан."""
    calculation_date = result.calculation_date or utcnow().date()
    existing = session.scalars(
        select(SourceConfidence)
        .where(SourceConfidence.source_id == source.id)
        .where(SourceConfidence.calculation_date == calculation_date)
    ).first()

    if existing is None:
        existing = SourceConfidence(
            source_id=source.id,
            calculation_date=calculation_date,
            created_at=utcnow(),
        )
        session.add(existing)

    existing.formula_version = result.formula_version
    existing.score = result.score
    existing.level = result.level.value
    existing.input_metrics = result.input_metrics
    existing.factor_scores = result.factor_scores
    session.flush()
    return existing


def calculate_all_source_confidence(
    session: Session, *, window_days: int = 30
) -> dict[str, Any]:
    """Ежедневный пересчет Source Confidence по всем источникам (DELTA §11.3)."""
    sources = session.scalars(select(Source)).all()
    calculation_date = utcnow().date()
    results: dict[str, Any] = {}

    for source in sources:
        result = compute_source_confidence(
            session, source, window_days=window_days, calculation_date=calculation_date
        )
        persist_source_confidence(session, source, result)
        results[source.code] = {"score": result.score, "level": result.level.value}

    logger.info(
        "Source Confidence пересчитан",
        sources=len(results),
        formula_version=CONFIDENCE_FORMULA_VERSION,
    )
    return {"calculation_date": calculation_date.isoformat(), "sources": results}


def latest_confidence(session: Session, source_id: Any) -> SourceConfidence | None:
    return session.scalars(
        select(SourceConfidence)
        .where(SourceConfidence.source_id == source_id)
        .order_by(SourceConfidence.calculation_date.desc())
        .limit(1)
    ).first()


def source_overview(session: Session, *, window_days: int = 30) -> list[dict[str, Any]]:
    """Данные экрана «Качество источников» (SCOPE-R P §15)."""
    sources = session.scalars(select(Source).order_by(Source.category, Source.code)).all()
    overview: list[dict[str, Any]] = []

    for source in sources:
        summary = source_metric_summary(session, source.id, window_days=window_days)
        confidence = latest_confidence(session, source.id)
        last_run = session.scalar(
            select(func.max(SourceMetric.observed_at)).where(SourceMetric.source_id == source.id)
        )
        overview.append(
            {
                "code": source.code,
                "name": source.name,
                "category": source.category,
                "offer_types": list(source.offer_types or []),
                "protocol": source.protocol,
                "is_enabled": source.is_enabled,
                "is_synthetic": source.is_synthetic,
                "qualification_status": source.qualification_status,
                "requires_credentials": source.requires_credentials,
                "last_run_at": last_run.isoformat() if last_run else None,
                "last_success_at": source.last_success_at.isoformat()
                if source.last_success_at
                else None,
                "last_error": source.last_error,
                "circuit_open_until": source.circuit_open_until.isoformat()
                if source.circuit_open_until
                else None,
                "consecutive_failures": source.consecutive_failures,
                "success_rate": summary["success_rate"],
                "avg_latency_ms": summary["avg_latency_ms"],
                "call_count": summary["call_count"],
                "total_offers": summary["total_offers"],
                "valid_offer_ratio": summary["valid_offer_ratio"],
                "field_completeness": summary["field_completeness"],
                "error_breakdown": summary.get("error_breakdown", {}),
                "confidence_score": confidence.effective_score if confidence else None,
                "confidence_level": confidence.level if confidence else None,
                "confidence_date": confidence.calculation_date.isoformat()
                if confidence
                else None,
                "min_supported_date": source.min_supported_date.isoformat()
                if source.min_supported_date
                else None,
                "max_supported_date": source.max_supported_date.isoformat()
                if source.max_supported_date
                else None,
                "booking_horizon_days": source.booking_horizon_days,
            }
        )
    return overview
