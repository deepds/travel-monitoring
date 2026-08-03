"""Celery-задачи обслуживания: health check, retention, метрики, экспорт."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from celery import shared_task
from sqlalchemy import select

from tco.core.config import get_settings
from tco.core.enums import JobStatus, SourceStatus
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.cache.result_cache import get_result_cache
from tco.connectors.registry import build_context, create_connector
from tco.db.models.source import Source, SourceMetric
from tco.db.session import session_scope
from tco.services import jobs as job_service
from tco.services import retention
from tco.services.source_metrics import calculate_all_source_confidence

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Источники
# --------------------------------------------------------------------------- #


def _run_health_check(session, source: Source) -> dict[str, Any]:  # noqa: ANN001
    """Выполняет health check и обновляет состояние источника."""
    settings = get_settings()
    context = build_context(
        code=source.code,
        name=source.name,
        allowed_hosts=source.allowed_hosts,
        config=dict(source.config or {}),
        settings=settings,
    )
    connector = create_connector(source.code, context)
    result = connector.health_check()
    now = utcnow()

    if result.outcome.is_ok:
        source.last_success_at = now
        source.consecutive_failures = 0
        source.circuit_open_until = None
        source.last_error = None
    else:
        source.last_failure_at = now
        source.last_error = (result.error_message or result.outcome.value)[:1024]

    session.add(
        SourceMetric(
            source_id=source.id,
            offer_type="HEALTH_CHECK",
            observed_at=now,
            outcome=result.outcome.value,
            latency_ms=result.latency_ms,
            attempts=result.attempts,
            error_code=result.error_code,
            error_message=result.error_message,
            connector_version=result.connector_version,
        )
    )
    return {
        "source": source.code,
        "outcome": result.outcome.value,
        "latency_ms": result.latency_ms,
        "error": result.error_message,
        "diagnostics": result.diagnostics,
    }


@shared_task(name="tco.source.health_check_source")
def health_check_source(source_id: str) -> dict[str, Any]:
    """Проверка доступности одного источника."""
    with session_scope() as session:
        source = session.get(Source, uuid.UUID(str(source_id)))
        if source is None:
            raise ValueError(f"Источник {source_id} не найден")
        return _run_health_check(session, source)


@shared_task(name="tco.source.health_check_all_sources")
def health_check_all_sources() -> dict[str, Any]:
    """Периодическая проверка всех включенных источников."""
    with session_scope() as session:
        sources = session.scalars(select(Source).where(Source.is_enabled.is_(True))).all()
        results = [_run_health_check(session, source) for source in sources]
    healthy = sum(1 for item in results if item["outcome"] in ("SUCCESS", "EMPTY"))
    logger.info("Health check источников завершен", total=len(results), healthy=healthy)
    return {"checked": len(results), "healthy": healthy, "results": results}


@shared_task(name="tco.source.qualify_source")
def qualify_source(source_id: str, approve: bool = False) -> dict[str, Any]:
    """Проверка пригодности источника (Source Qualification Spike).

    Выполняет health check и фиксирует технический горизонт. Перевод
    в ``APPROVED`` — только по явному решению администратора: автоматическая
    квалификация исказила бы смысл go/no-go.
    """
    with session_scope() as session:
        source = session.get(Source, uuid.UUID(str(source_id)))
        if source is None:
            raise ValueError(f"Источник {source_id} не найден")

        health = _run_health_check(session, source)
        configured = not source.requires_credentials or bool(
            create_connector(
                source.code,
                build_context(
                    code=source.code,
                    name=source.name,
                    allowed_hosts=source.allowed_hosts,
                    config=dict(source.config or {}),
                ),
            ).is_configured()[0]
        )
        source.qualified_at = utcnow()
        if approve and health["outcome"] in ("SUCCESS", "EMPTY") and configured:
            source.qualification_status = SourceStatus.APPROVED.value

        return {
            **health,
            "configured": configured,
            "qualification_status": source.qualification_status,
        }


@shared_task(name="tco.source.refresh_source_horizons")
def refresh_source_horizons() -> dict[str, Any]:
    """Обновляет технический горизонт источников (SCOPE-R C §7)."""
    today = utcnow().date()
    updated: list[str] = []
    with session_scope() as session:
        for source in session.scalars(select(Source)).all():
            if not source.booking_horizon_days:
                continue
            source.min_supported_date = today
            source.max_supported_date = today + timedelta(days=int(source.booking_horizon_days))
            source.horizon_checked_at = utcnow()
            updated.append(source.code)
    return {"updated": updated, "date": today.isoformat()}


# --------------------------------------------------------------------------- #
# Метрики
# --------------------------------------------------------------------------- #


@shared_task(name="tco.metrics.calculate_source_confidence_all")
def calculate_source_confidence_all(window_days: int = 30) -> dict[str, Any]:
    """Ежедневный пересчет Source Confidence."""
    with session_scope() as session:
        return calculate_all_source_confidence(session, window_days=window_days)


@shared_task(name="tco.metrics.calculate_quality_metrics")
def calculate_quality_metrics(window_days: int = 7) -> dict[str, Any]:
    """Сводные KPI стабильности за окно (SCOPE-R O §2)."""
    from tco.services.dashboard import kpi_summary

    with session_scope() as session:
        return kpi_summary(session, window_days=window_days)


# --------------------------------------------------------------------------- #
# Обслуживание
# --------------------------------------------------------------------------- #


@shared_task(name="tco.maintenance.cleanup_expired_raw_data")
def cleanup_expired_raw_data() -> dict[str, int]:
    with session_scope() as session:
        return retention.cleanup_expired_raw_data(session)


@shared_task(name="tco.maintenance.cleanup_expired_cache")
def cleanup_expired_cache() -> dict[str, int]:
    with session_scope() as session:
        return retention.cleanup_expired_cache(session)


@shared_task(name="tco.maintenance.cleanup_expired_data")
def cleanup_expired_data() -> dict[str, int]:
    """Полный цикл retention: raw, HTML, offers, кэш, экспорты, сценарии."""
    with session_scope() as session:
        return retention.run_all_retention(session)


@shared_task(name="tco.maintenance.detect_stalled_jobs")
def detect_stalled_jobs(stale_after_seconds: int = 900) -> dict[str, Any]:
    """Обнаружение зависших задач (DELTA §11.2)."""
    with session_scope() as session:
        stalled = job_service.detect_stalled_jobs(
            session, stale_after_seconds=stale_after_seconds
        )
        return {"stalled": [str(job.id) for job in stalled], "count": len(stalled)}


@shared_task(name="tco.maintenance.purge_result_cache")
def purge_result_cache() -> dict[str, int]:
    """Административный force refresh: полная очистка кэша результатов."""
    with session_scope() as session:
        return {"purged": get_result_cache().purge_all(session)}


# --------------------------------------------------------------------------- #
# Экспорт
# --------------------------------------------------------------------------- #


@shared_task(name="tco.export.export_dataset", bind=True)
def export_dataset(self, job_id: str) -> dict[str, Any]:  # noqa: ANN001
    """Фоновая выгрузка набора данных в CSV/XLSX."""
    from tco.services.export import run_export_job

    with session_scope() as session:
        job = job_service.get_job(session, job_id)
        job_service.transition(session, job, JobStatus.RUNNING, message="Экспорт запущен")

    try:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            artifact = run_export_job(session, job)
            job_service.transition(
                session,
                job,
                JobStatus.SUCCESS,
                message="Экспорт завершен",
                result={
                    "artifact_id": str(artifact.id),
                    "filename": artifact.filename,
                    "row_count": artifact.row_count,
                    "size_bytes": artifact.size_bytes,
                },
            )
            return {"artifact_id": str(artifact.id), "row_count": artifact.row_count}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Экспорт завершился ошибкой", job_id=job_id, error=str(exc))
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            job_service.transition(
                session,
                job,
                JobStatus.FAILED,
                message="Ошибка экспорта",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        raise


# --------------------------------------------------------------------------- #
# Импорт сценариев
# --------------------------------------------------------------------------- #


@shared_task(name="tco.maintenance.import_scenarios_job", bind=True)
def import_scenarios_job(self, job_id: str) -> dict[str, Any]:  # noqa: ANN001
    """Фоновый импорт каталога сценариев из CSV/YAML."""
    from tco.services.scenarios import import_scenarios

    with session_scope() as session:
        job = job_service.get_job(session, job_id)
        job_service.transition(session, job, JobStatus.RUNNING, message="Импорт запущен")
        params = dict(job.params or {})

    try:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            report = import_scenarios(
                session,
                params["content"],
                fmt=params.get("format", "csv"),
                created_by=job.created_by,
                source_file=params.get("filename"),
            )
            status = JobStatus.PARTIAL if report.errors else JobStatus.SUCCESS
            job_service.transition(
                session,
                job,
                status,
                message=f"Импортировано: {report.created}, ошибок: {len(report.errors)}",
                result=report.as_dict(),
            )
            return report.as_dict()
    except Exception as exc:  # noqa: BLE001
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            job_service.transition(
                session,
                job,
                JobStatus.FAILED,
                message="Ошибка импорта",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        raise
