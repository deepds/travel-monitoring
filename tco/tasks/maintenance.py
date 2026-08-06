"""Celery-задачи обслуживания: health check, retention, метрики, экспорт."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from celery import group, shared_task
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


#: Сколько подряд циклов застоя терпеть, прежде чем перезапускать пул.
_WATCHDOG_PATIENCE = 2
#: Ключ, которым сторож помнит предыдущие циклы. Redis, а не память процесса:
#: задача выполняется в пуле и может достаться другому процессу.
_WATCHDOG_KEY = "tco:watchdog:collect_stall_cycles"


def _watchdog_state(delta: int) -> int:
    """Счетчик подряд идущих циклов застоя. ``delta=0`` сбрасывает."""
    settings = get_settings()
    if not settings.redis_url:
        return delta
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0
        )
        try:
            if delta <= 0:
                client.delete(_WATCHDOG_KEY)
                return 0
            value = int(client.incr(_WATCHDOG_KEY))
            # Счетчик не должен переживать сутки: застой, о котором забыли,
            # иначе сложился бы со следующим и дал ложный перезапуск.
            client.expire(_WATCHDOG_KEY, 6 * 3600)
            return value
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 — сторож не должен падать сам
        logger.warning("Состояние сторожа недоступно", error=str(exc))
        return delta


@shared_task(name="tco.maintenance.watch_collection_progress")
def watch_collection_progress(stale_minutes: int = 15) -> dict[str, Any]:
    """Сторож застрявшего сбора.

    Работает в отдельном воркере обслуживания — в том самом, который не занят
    сбором. Это и есть смысл разделения: шестого августа один воркер держал
    очереди ``collect``, ``compute``, ``ondemand`` и ``maintenance`` разом,
    поэтому забитый сбором пул не выполнял и детектор зависших задач. Чинить
    было некому.

    Обнаружив, что очередь сбора не разбирается, сторож просит воркеров
    перезапустить пул: рабочие процессы поднимаются заново, задачи с
    подтверждением после выполнения (``acks_late``) возвращаются в очередь.
    Если это не помогло за два цикла, лечение переходит к следующей линии —
    проверка живости объявит контейнер нездоровым, и его перезапустит
    ``autoheal``.
    """
    from tco.tasks.celery_app import celery_app
    from tco.tasks.health import check

    healthy, details = check(["collect", "compute"], stale_minutes=stale_minutes)
    if healthy:
        _watchdog_state(0)
        return {"stalled": False, **details}

    cycles = _watchdog_state(1)
    payload: dict[str, Any] = {"stalled": True, "cycles": cycles, **details}

    if cycles <= _WATCHDOG_PATIENCE:
        logger.warning("Сбор не движется — перезапуск пула воркеров", **payload)
        try:
            replies = celery_app.control.broadcast(
                "pool_restart", arguments={"reload": False}, reply=True, timeout=10
            )
            payload["pool_restart_replies"] = len(replies or [])
        except Exception as exc:  # noqa: BLE001 — брокер мог отвалиться вместе с воркером
            logger.error("Не удалось перезапустить пул", error=str(exc), **payload)
            payload["pool_restart_error"] = str(exc)
    else:
        # Перезапуск пула не помог: дальше действует проверка живости
        # контейнера и autoheal. Сторож только фиксирует, что дошло до этого.
        logger.error(
            "Перезапуск пула не помог — ждем перезапуска контейнера", **payload
        )
    return payload


@shared_task(name="tco.monitoring.backfill_missing_observations", bind=True)
def backfill_missing_observations(
    self,  # noqa: ANN001
    lookback_days: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """Досбор дыр: повторяет сценарии, оставшиеся сегодня без наблюдения.

    Штатный шаг суточного цикла, а не аварийная мера. Часть сценариев
    неизбежно выпадает: источник ответил таймаутом, размыкатель цепи был
    разомкнут, воркер перезапустился на середине. К моменту досбора размыкатель
    остывает (900 секунд по умолчанию), и повтор обычно проходит.

    Дырой считается сценарий сетки без успешного снимка за сегодня. Снимок с
    предложениями, но неудачным расчетом, дырой не считается: пересчитать его
    можно и без обращения к источникам.
    """
    from tco.core.enums import ScenarioType, SnapshotStatus
    from tco.db.models.scenario import TravelScenario
    from tco.db.models.snapshot import MarketSnapshot
    from tco.tasks.pipeline import refresh_monitoring_scenario

    today = utcnow().date() - timedelta(days=max(0, lookback_days))

    with session_scope() as session:
        collected = select(MarketSnapshot.scenario_id).where(
            MarketSnapshot.observation_date >= today,
            MarketSnapshot.status.in_(
                [SnapshotStatus.COMPLETE.value, SnapshotStatus.PARTIAL.value]
            ),
        )
        missing = session.scalars(
            select(TravelScenario)
            .where(TravelScenario.scenario_type == ScenarioType.MONITORING.value)
            .where(TravelScenario.is_active.is_(True))
            .where(TravelScenario.deleted_at.is_(None))
            .where(TravelScenario.is_showcase_grid.is_(True))
            .where(TravelScenario.id.not_in(collected))
            .order_by(TravelScenario.departure_date, TravelScenario.code)
        ).all()
        scenario_ids = [str(item.id) for item in missing]

    if limit:
        scenario_ids = scenario_ids[:limit]
    if not scenario_ids:
        logger.info("Досбор не потребовался: дыр нет", date=today.isoformat())
        return {"missing": 0, "dispatched": 0, "date": today.isoformat()}

    # Принудительный сбор: снимок за это окно уже мог быть заведен неудачной
    # попыткой, и без ``force_refresh`` повтор вернул бы SKIPPED_IDEMPOTENT.
    dispatched = group(
        refresh_monitoring_scenario.s(scenario_id, None, True, None)
        for scenario_id in scenario_ids
    ).apply_async()

    logger.info(
        "Досбор дыр запущен",
        date=today.isoformat(),
        scenarios=len(scenario_ids),
        group_id=str(dispatched.id),
    )
    return {
        "missing": len(scenario_ids),
        "dispatched": len(scenario_ids),
        "date": today.isoformat(),
        "group_id": str(dispatched.id),
    }


@shared_task(name="tco.metrics.daily_collection_report")
def daily_collection_report(day_offset: int = 0) -> dict[str, Any]:
    """Сводка качества суточного прогона.

    Прогон, потерявший больше пяти процентов сценариев, должен быть виден без
    чтения логов — иначе о потере узнают по дырам на витрине через сутки.
    """
    from tco.services.coverage import daily_run_summary

    day = utcnow().date() - timedelta(days=max(0, day_offset))
    with session_scope() as session:
        report = daily_run_summary(session, day=day)

    if report["missing_ratio"] > 0.05:
        logger.error("Суточный прогон потерял больше 5 % сценариев", **report)
    else:
        logger.info("Суточный прогон завершен", **report)
    return report


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
