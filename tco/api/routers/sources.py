"""Управление источниками данных и Source Confidence (DELTA §6.11, §7)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from tco.api.deps import AdminDep, SessionDep, ViewerDep
from tco.api.serializers import source_confidence, source_full, source_metric
from tco.core.enums import AuditAction, SourceCategory, SourceStatus
from tco.core.errors import NotFoundError, ValidationError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.source import Source, SourceConfidence, SourceMetric
from tco.services import audit
from tco.services.source_metrics import (
    compute_source_confidence,
    latest_confidence,
    persist_source_confidence,
    source_metric_summary,
    source_overview,
)
from tco.version import CONFIDENCE_FORMULA_VERSION

logger = get_logger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])


class SourcePatch(BaseModel):
    """Изменяемые характеристики источника.

    Базовый адрес и учетные данные приходят только из конфигурации окружения —
    через API их изменить нельзя (запрет произвольных URL в коннекторах).
    """

    name: str | None = Field(None, max_length=255)
    qualification_status: SourceStatus | None = None
    qualification_notes: str | None = Field(None, max_length=4096)
    legal_status: str | None = Field(None, max_length=64)
    storage_allowed: bool | None = None
    html_storage_allowed: bool | None = None
    min_supported_date: date | None = None
    max_supported_date: date | None = None
    booking_horizon_days: int | None = Field(None, ge=0, le=1095)
    rate_limit_per_minute: int | None = Field(None, ge=0, le=100_000)
    supported_city_codes: list[str] | None = None


class ConfidenceOverride(BaseModel):
    """Ручной override Source Confidence — обязательно аудируется (DELTA §7.5)."""

    score: float = Field(ge=0, le=100)
    reason: str = Field(min_length=3, max_length=1024)


@router.get("", summary="Список источников")
def list_sources(
    session: SessionDep,
    _: ViewerDep,
    category: SourceCategory | None = None,
    is_enabled: bool | None = None,
    qualification_status: SourceStatus | None = None,
    include_synthetic: bool = True,
) -> dict[str, Any]:
    stmt = select(Source).order_by(Source.category, Source.code)
    if category:
        stmt = stmt.where(Source.category == category.value)
    if is_enabled is not None:
        stmt = stmt.where(Source.is_enabled.is_(is_enabled))
    if qualification_status:
        stmt = stmt.where(Source.qualification_status == qualification_status.value)
    if not include_synthetic:
        stmt = stmt.where(Source.is_synthetic.is_(False))

    rows = session.scalars(stmt).all()
    return {"items": [source_full(row) for row in rows], "total": len(rows)}


@router.get("/overview", summary="Экран качества источников")
def overview(
    session: SessionDep,
    _: ViewerDep,
    window_days: Annotated[int, Query(ge=1, le=180)] = 30,
) -> dict[str, Any]:
    """Сводка для экрана «Качество источников»: успешность, latency, доверие."""
    items = source_overview(session, window_days=window_days)
    return {"items": items, "total": len(items), "window_days": window_days}


@router.get("/{source_id}", summary="Источник")
def get_source(source_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    source = _resolve(session, source_id)
    confidence = latest_confidence(session, source.id)
    return {
        **source_full(source),
        "confidence": source_confidence(confidence) if confidence else None,
    }


@router.patch("/{source_id}", summary="Изменить источник")
def patch_source(
    source_id: str,
    payload: SourcePatch,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, Any]:
    source = _resolve(session, source_id)
    changes = payload.model_dump(exclude_unset=True)

    for field_name, value in changes.items():
        setattr(source, field_name, value.value if hasattr(value, "value") else value)
    if "qualification_status" in changes:
        source.qualified_at = utcnow()
    source.updated_at = utcnow()

    audit.record(
        session,
        AuditAction.SOURCE_UPDATE,
        principal=principal,
        object_type="Source",
        object_id=str(source.id),
        summary=f"Изменен источник {source.code}",
        payload={"changes": {k: str(v) for k, v in changes.items()}},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return source_full(source)


@router.post("/{source_id}/enable", summary="Включить источник")
def enable(
    source_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    return _toggle(session, source_id, True, principal, request)


@router.post("/{source_id}/disable", summary="Выключить источник")
def disable(
    source_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    return _toggle(session, source_id, False, principal, request)


@router.post("/{source_id}/health-check", summary="Проверить доступность источника")
def health_check(
    source_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    """Выполняет технический health check и сбрасывает предохранитель при успехе."""
    source = _resolve(session, source_id)
    source_id_str = str(source.id)

    audit.record(
        session,
        AuditAction.SOURCE_HEALTH_CHECK,
        principal=principal,
        object_type="Source",
        object_id=source_id_str,
        summary=f"Запущена проверка источника {source.code}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()

    from tco.tasks.maintenance import health_check_source

    result = health_check_source.apply(args=[source_id_str]).get()
    session.expire_all()
    source = _resolve(session, source_id)
    return {"source": source_full(source), "check": result}


@router.get("/{source_id}/metrics", summary="Технические метрики источника")
def metrics(
    source_id: str,
    session: SessionDep,
    _: ViewerDep,
    window_days: Annotated[int, Query(ge=1, le=180)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    source = _resolve(session, source_id)
    summary = source_metric_summary(session, source.id, window_days=window_days)
    recent = session.scalars(
        select(SourceMetric)
        .where(SourceMetric.source_id == source.id)
        .order_by(SourceMetric.observed_at.desc())
        .limit(limit)
    ).all()
    return {
        "source": {"id": str(source.id), "code": source.code, "name": source.name},
        "summary": summary,
        "recent": [source_metric(row) for row in recent],
    }


@router.get("/{source_id}/confidence", summary="Source Confidence")
def confidence(
    source_id: str,
    session: SessionDep,
    _: ViewerDep,
    history_limit: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    """Долгосрочная степень доверия к источнику с объяснением факторов.

    Это не технический health и не Quality Score конкретного расчета.
    """
    source = _resolve(session, source_id)
    history = session.scalars(
        select(SourceConfidence)
        .where(SourceConfidence.source_id == source.id)
        .order_by(SourceConfidence.calculation_date.desc())
        .limit(history_limit)
    ).all()

    current = history[0] if history else None
    return {
        "source": {"id": str(source.id), "code": source.code, "name": source.name},
        "current": source_confidence(current) if current else None,
        "history": [source_confidence(row) for row in history],
        "formula_version": CONFIDENCE_FORMULA_VERSION,
        "levels": {"HIGH": "80–100", "MEDIUM": "60–79", "LOW": "40–59", "UNTRUSTED": "0–39"},
    }


@router.post("/{source_id}/confidence/recalculate", summary="Пересчитать Source Confidence")
def recalculate_confidence(
    source_id: str,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    window_days: Annotated[int, Query(ge=1, le=180)] = 30,
) -> dict[str, Any]:
    source = _resolve(session, source_id)
    result = compute_source_confidence(session, source, window_days=window_days)
    record = persist_source_confidence(session, source, result)
    session.commit()
    logger.info("Source Confidence пересчитан", source=source.code, score=record.score)
    return source_confidence(record)


@router.post("/{source_id}/confidence/override", summary="Ручной override Source Confidence")
def override_confidence(
    source_id: str,
    payload: ConfidenceOverride,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Задает ручное значение доверия. Оригинальный расчет сохраняется."""
    source = _resolve(session, source_id)
    record = latest_confidence(session, source.id)
    if record is None:
        raise ValidationError(
            "Нет рассчитанного Source Confidence — сначала выполните пересчет",
            details={"source_code": source.code},
        )

    record.manual_override = payload.score
    record.override_reason = payload.reason
    record.approved_by = principal.username
    record.overridden_at = utcnow()

    audit.record(
        session,
        AuditAction.SOURCE_CONFIDENCE_OVERRIDE,
        principal=principal,
        object_type="SourceConfidence",
        object_id=str(record.id),
        summary=f"Ручной override доверия источника {source.code}: {payload.score}",
        payload={
            "source_code": source.code,
            "calculated_score": record.score,
            "override_score": payload.score,
            "reason": payload.reason,
        },
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    logger.warning(
        "Установлен ручной override Source Confidence",
        source=source.code,
        score=payload.score,
        actor=principal.username,
    )
    return source_confidence(record)


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #


def _resolve(session: SessionDep, source_id: str) -> Source:
    """Источник доступен и по UUID, и по короткому коду."""
    found = session.scalars(select(Source).where(Source.code == source_id)).first()
    if found is not None:
        return found
    from tco.api.deps import get_or_404

    return get_or_404(session, Source, source_id, "Источник")


def _toggle(
    session: SessionDep, source_id: str, enabled: bool, principal: Any, request: Request
) -> dict[str, Any]:
    source = _resolve(session, source_id)
    if enabled and source.requires_credentials and not (source.config or {}).get("has_credentials"):
        logger.warning(
            "Источник включен без подтвержденных учетных данных", source=source.code
        )

    source.is_enabled = enabled
    source.updated_at = utcnow()
    if enabled:
        # Включение вручную закрывает предохранитель.
        source.consecutive_failures = 0
        source.circuit_open_until = None

    audit.record(
        session,
        AuditAction.SOURCE_ENABLE if enabled else AuditAction.SOURCE_DISABLE,
        principal=principal,
        object_type="Source",
        object_id=str(source.id),
        summary=f"Источник {source.code} {'включен' if enabled else 'выключен'}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    logger.info(
        "Изменена активность источника", source=source.code, enabled=enabled, actor=principal.username
    )
    return source_full(source)
