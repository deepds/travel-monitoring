"""Celery-задачи конвейера: сбор, снимок, расчет, мониторинг.

Карта задач и соответствие требованиям DELTA §4.2 — в
``docs/CELERY_TASK_MAP.md``.

Оркестрация плановых снимков (DELTA §4.3):

    chord(
        group(collect_transport_offers, collect_accommodation_offers)
    ) -> build_market_snapshot -> calculate_scenario_run -> update_cache

Задачи не возвращают крупные объекты: параллельные ветви пишут результат в
БД и raw storage, а через result backend передают только компактные сводки.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import chain, chord, group, shared_task
from sqlalchemy import select

from tco.core.config import get_settings
from tco.core.enums import (
    JobStatus,
    JobType,
    OfferType,
    RunType,
    ScenarioType,
    SnapshotType,
)
from tco.core.logging import correlation_id_var, get_logger
from tco.core.utils import utcnow
from tco.cache.result_cache import get_result_cache
from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot
from tco.db.session import session_scope
from tco.engine.fingerprint import job_idempotency_key
from tco.schemas.profile import ProfileRules
from tco.services import jobs as job_service
from tco.services.calculation import (
    active_profile,
    calculate_from_snapshot,
    calculate_scenario,
    scenario_key,
    validate,
)
from tco.engine.fingerprint import cache_key as build_cache_key
from tco.services.snapshot_builder import (
    collect_into_snapshot,
    create_snapshot,
    finalize_snapshot,
    transport_offer_type_for,
)

logger = get_logger(__name__)


def _load(session, scenario_id: str) -> TravelScenario:  # noqa: ANN001
    scenario = session.get(TravelScenario, uuid.UUID(str(scenario_id)))
    if scenario is None:
        raise ValueError(f"Сценарий {scenario_id} не найден")
    return scenario


def _profile(session, profile_id: str | None, scenario: TravelScenario | None):  # noqa: ANN001
    if profile_id:
        profile = session.get(CalculationProfile, uuid.UUID(str(profile_id)))
        if profile is None:
            raise ValueError(f"Профиль {profile_id} не найден")
        return profile
    return active_profile(session, scenario)


# --------------------------------------------------------------------------- #
# Сбор по компонентам (ветви chord)
# --------------------------------------------------------------------------- #


@shared_task(name="tco.collect.collect_transport_offers", bind=True, max_retries=2)
def collect_transport_offers(
    self, snapshot_id: str, profile_id: str | None = None  # noqa: ANN001
) -> dict[str, Any]:
    """Собирает транспортные предложения в существующий снимок."""
    return _collect_component(snapshot_id, profile_id, component="TRANSPORT")


@shared_task(name="tco.collect.collect_accommodation_offers", bind=True, max_retries=2)
def collect_accommodation_offers(
    self, snapshot_id: str, profile_id: str | None = None  # noqa: ANN001
) -> dict[str, Any]:
    """Собирает предложения по проживанию в существующий снимок."""
    return _collect_component(snapshot_id, profile_id, component="ACCOMMODATION")


def _collect_component(snapshot_id: str, profile_id: str | None, component: str) -> dict[str, Any]:
    with session_scope() as session:
        snapshot = session.get(MarketSnapshot, uuid.UUID(str(snapshot_id)))
        if snapshot is None:
            raise ValueError(f"Снимок {snapshot_id} не найден")
        scenario = _load(session, str(snapshot.scenario_id))
        profile = _profile(session, profile_id, scenario)
        rules = ProfileRules.parse(profile.rules)

        if component == "TRANSPORT":
            transport_type = transport_offer_type_for(scenario)
            offer_types = (transport_type,) if transport_type else ()
        else:
            offer_types = (OfferType.ACCOMMODATION,) if scenario.observes_accommodation else ()

        # Сценарий может наблюдать только одну компоненту: к источникам за
        # второй обращаться незачем, ветвь chord завершается сразу.
        if not offer_types:
            return {
                "snapshot_id": str(snapshot.id),
                "component": component,
                "offers": 0,
                "errors": 0,
                "skipped": "компонента не наблюдается сценарием",
            }

        result = collect_into_snapshot(
            session, snapshot, scenario, rules=rules, offer_types=offer_types
        )
        return {
            "snapshot_id": str(snapshot.id),
            "component": component,
            "offers": len(result.offers),
            "errors": len(result.errors),
        }


# --------------------------------------------------------------------------- #
# Снимок и расчет
# --------------------------------------------------------------------------- #


@shared_task(name="tco.snapshot.build_market_snapshot", bind=True)
def build_market_snapshot(
    self,  # noqa: ANN001
    collect_results: list[dict[str, Any]] | None = None,
    snapshot_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Callback chord: финализирует снимок после параллельного сбора."""
    if snapshot_id is None:
        raise ValueError("build_market_snapshot требует snapshot_id")
    with session_scope() as session:
        snapshot = session.get(MarketSnapshot, uuid.UUID(str(snapshot_id)))
        if snapshot is None:
            raise ValueError(f"Снимок {snapshot_id} не найден")
        finalize_snapshot(session, snapshot)
        return {
            "snapshot_id": str(snapshot.id),
            "status": snapshot.status,
            "transport_offers": snapshot.transport_offer_count,
            "accommodation_offers": snapshot.accommodation_offer_count,
            "collect_results": collect_results or [],
        }


@shared_task(name="tco.calculate.calculate_scenario_run", bind=True)
def calculate_scenario_run(
    self,  # noqa: ANN001
    upstream: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
    profile_id: str | None = None,
    run_type: str = RunType.MONITORING.value,
    job_id: str | None = None,
    created_by: str | None = None,
    update_cache: bool = True,
) -> dict[str, Any]:
    """Применяет методику к снимку и создает ScenarioRun."""
    snapshot_id = snapshot_id or (upstream or {}).get("snapshot_id")
    if not snapshot_id:
        raise ValueError("calculate_scenario_run требует snapshot_id")

    with session_scope() as session:
        snapshot = session.get(MarketSnapshot, uuid.UUID(str(snapshot_id)))
        if snapshot is None:
            raise ValueError(f"Снимок {snapshot_id} не найден")
        scenario = _load(session, str(snapshot.scenario_id))
        profile = _profile(session, profile_id, scenario)
        rules = ProfileRules.parse(profile.rules)
        key = build_cache_key(scenario_key(scenario), profile.version, profile.code)

        result = calculate_from_snapshot(
            session,
            snapshot,
            profile,
            run_type=RunType(run_type),
            created_by=created_by,
            job_id=uuid.UUID(job_id) if job_id else None,
            cache_key=key,
        )

        if update_cache:
            get_result_cache().put(
                session,
                cache_key=key,
                scenario_fingerprint=scenario.fingerprint,
                profile_version=profile.version,
                scenario_run_id=result.run.id,
                market_snapshot_id=snapshot.id,
                payload={
                    "status": result.run.status,
                    "quality_score": result.run.quality_score,
                    "confidence_level": result.run.confidence_level,
                },
                ttl_minutes=rules.limits.result_cache_ttl_minutes,
            )

        if job_id:
            job = session.get(job_service.Job, uuid.UUID(job_id))
            if job is not None:
                job.market_snapshot_id = snapshot.id
                job.scenario_run_id = result.run.id
                job_service.transition(
                    session,
                    job,
                    JobStatus.SUCCESS,
                    message="Расчет завершен",
                    result={
                        "scenario_run_id": str(result.run.id),
                        "market_snapshot_id": str(snapshot.id),
                        "status": result.run.status,
                        "quality_score": result.run.quality_score,
                    },
                )

        return {
            "scenario_run_id": str(result.run.id),
            "snapshot_id": str(snapshot.id),
            "status": result.run.status,
            "quality_score": result.run.quality_score,
            "confidence_level": result.run.confidence_level,
            "total_estimated_cost": float(result.run.total_estimated_cost)
            if result.run.total_estimated_cost is not None
            else None,
        }


@shared_task(name="tco.calculate.replay_snapshot_with_profile", bind=True)
def replay_snapshot_with_profile(
    self, snapshot_id: str, profile_id: str, created_by: str | None = None, job_id: str | None = None  # noqa: ANN001
) -> dict[str, Any]:
    """Пересчитывает существующий снимок другим профилем.

    Исходный снимок не изменяется — создается новый ``ScenarioRun``
    (DELTA §6.8, §9.3).
    """
    with session_scope() as session:
        snapshot = session.get(MarketSnapshot, uuid.UUID(str(snapshot_id)))
        if snapshot is None:
            raise ValueError(f"Снимок {snapshot_id} не найден")
        if not snapshot.offers_available:
            raise ValueError(
                "Предложения снимка удалены политикой retention — пересчет невозможен"
            )
        profile = _profile(session, profile_id, None)
        result = calculate_from_snapshot(
            session,
            snapshot,
            profile,
            run_type=RunType.REPLAY,
            created_by=created_by,
            job_id=uuid.UUID(job_id) if job_id else None,
        )
        if job_id:
            job = session.get(job_service.Job, uuid.UUID(job_id))
            if job is not None:
                job.scenario_run_id = result.run.id
                job.market_snapshot_id = snapshot.id
                job_service.transition(
                    session,
                    job,
                    JobStatus.SUCCESS,
                    message="Пересчет снимка завершен",
                    result={"scenario_run_id": str(result.run.id)},
                )
        return {
            "scenario_run_id": str(result.run.id),
            "snapshot_id": str(snapshot.id),
            "profile": profile.label,
            "status": result.run.status,
        }


# --------------------------------------------------------------------------- #
# On-demand
# --------------------------------------------------------------------------- #


@shared_task(name="tco.ondemand.run_on_demand_calculation", bind=True)
def run_on_demand_calculation(
    self,  # noqa: ANN001
    job_id: str,
    scenario_id: str,
    profile_id: str | None = None,
    force_refresh: bool = False,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Полный On-demand расчет в фоне: API не блокируется (DELTA §11.2)."""
    with session_scope() as session:
        job = job_service.get_job(session, job_id)
        correlation_id_var.set(job.correlation_id)
        job_service.transition(session, job, JobStatus.RUNNING, message="Расчет запущен")

    try:
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            scenario = _load(session, scenario_id)
            profile = _profile(session, profile_id, scenario)
            outcome = calculate_scenario(
                session,
                scenario,
                profile=profile,
                run_type=RunType.ON_DEMAND,
                force_refresh=force_refresh,
                created_by=created_by,
                job_id=job.id,
            )

            if outcome.run is None:
                validation = outcome.validation.as_dict() if outcome.validation else {}
                job_service.transition(
                    session,
                    job,
                    JobStatus.FAILED,
                    message="Сценарий не поддерживается",
                    error_code="SCENARIO_UNSUPPORTED",
                    error_message=outcome.validation.summary if outcome.validation else None,
                    result={"validation": validation},
                )
                return {"status": "UNSUPPORTED", "validation": validation}

            job.market_snapshot_id = outcome.snapshot.id if outcome.snapshot else None
            job.scenario_run_id = outcome.run.id
            job_service.transition(
                session,
                job,
                JobStatus.SUCCESS,
                message="Расчет завершен" + (" (из кэша)" if outcome.from_cache else ""),
                result={
                    "scenario_run_id": str(outcome.run.id),
                    "market_snapshot_id": str(outcome.snapshot.id) if outcome.snapshot else None,
                    "status": outcome.run.status,
                    "cached": outcome.from_cache,
                    "quality_score": outcome.run.quality_score,
                    "confidence_level": outcome.run.confidence_level,
                },
            )
            return {
                "scenario_run_id": str(outcome.run.id),
                "status": outcome.run.status,
                "cached": outcome.from_cache,
            }
    except Exception as exc:  # noqa: BLE001 — итог обязан попасть в задачу
        logger.exception("On-demand расчет завершился ошибкой", job_id=job_id, error=str(exc))
        with session_scope() as session:
            job = job_service.get_job(session, job_id)
            job_service.transition(
                session,
                job,
                JobStatus.FAILED,
                message="Ошибка расчета",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        raise


# --------------------------------------------------------------------------- #
# Мониторинг
# --------------------------------------------------------------------------- #


@shared_task(name="tco.monitoring.refresh_monitoring_scenario", bind=True)
def refresh_monitoring_scenario(
    self,  # noqa: ANN001
    scenario_id: str,
    profile_id: str | None = None,
    force_refresh: bool = False,
    parent_job_id: str | None = None,
) -> dict[str, Any]:
    """Плановый снимок и расчет одного сценария мониторинга.

    При доступном брокере сбор компонентов идет параллельно через ``chord``;
    в eager-режиме выполняется тот же путь синхронно.
    """
    settings = get_settings()
    with session_scope() as session:
        scenario = _load(session, scenario_id)
        profile = _profile(session, profile_id, scenario)

        validation = validate(session, scenario, profile)
        if not validation.is_valid:
            logger.info(
                "Сценарий мониторинга не прошел валидацию",
                scenario=scenario.code,
                errors=[issue.code for issue in validation.errors],
            )
            return {
                "scenario": scenario.code,
                "status": "UNSUPPORTED",
                "errors": [issue.code for issue in validation.errors],
            }

        handle = job_service.create_job(
            session,
            job_type=JobType.MONITORING_SCENARIO,
            idempotency_key=job_idempotency_key(
                fingerprint=scenario.fingerprint,
                requested_at=utcnow(),
                bucket_hours=settings.snapshot_interval_hours,
                profile_version=profile.version,
                run_type=RunType.MONITORING,
            ),
            params={"scenario_id": str(scenario.id), "profile_id": str(profile.id)},
            scenario_id=scenario.id,
            parent_job_id=uuid.UUID(parent_job_id) if parent_job_id else None,
            created_by="scheduler",
        )
        job = handle.job
        if not handle.created and job.is_terminal and not force_refresh:
            return {
                "scenario": scenario.code,
                "status": "SKIPPED_IDEMPOTENT",
                "job_id": str(job.id),
            }

        job_service.transition(session, job, JobStatus.RUNNING, message="Плановый снимок")
        shell = create_snapshot(
            session,
            scenario,
            snapshot_type=SnapshotType.DAILY_MONITORING,
            force_refresh=force_refresh,
            created_by="scheduler",
            job_id=job.id,
            settings=settings,
        )
        snapshot_id = str(shell.snapshot.id)
        job.market_snapshot_id = shell.snapshot.id
        job_id = str(job.id)
        profile_id_str = str(profile.id)
        scenario_code = scenario.code
        already_collected = not shell.created

    if already_collected:
        # Снимок уже собран в этом окне — считаем по нему без нового сбора.
        result = calculate_scenario_run.apply(
            kwargs={
                "snapshot_id": snapshot_id,
                "profile_id": profile_id_str,
                "run_type": RunType.MONITORING.value,
                "job_id": job_id,
                "created_by": "scheduler",
            }
        ).get()
        return {"scenario": scenario_code, "reused_snapshot": True, **result}

    workflow = chord(
        group(
            collect_transport_offers.s(snapshot_id, profile_id_str),
            collect_accommodation_offers.s(snapshot_id, profile_id_str),
        ),
        chain(
            build_market_snapshot.s(snapshot_id=snapshot_id),
            calculate_scenario_run.s(
                profile_id=profile_id_str,
                run_type=RunType.MONITORING.value,
                job_id=job_id,
                created_by="scheduler",
            ),
        ),
    )
    async_result = workflow.apply_async()
    return {
        "scenario": scenario_code,
        "snapshot_id": snapshot_id,
        "job_id": job_id,
        "workflow_id": str(async_result.id),
        "status": "DISPATCHED",
    }


@shared_task(name="tco.monitoring.refresh_all_monitoring_scenarios", bind=True)
def refresh_all_monitoring_scenarios(
    self, profile_id: str | None = None, force_refresh: bool = False, limit: int | None = None  # noqa: ANN001
) -> dict[str, Any]:
    """Плановый прогон всех активных сценариев мониторинга.

    Параллелизм — на уровне сценариев: это и есть узкое место при 100+
    сценариях, четыре раза в сутки.
    """
    settings = get_settings()
    today = utcnow().date()

    with session_scope() as session:
        scenarios = session.scalars(
            select(TravelScenario)
            .where(TravelScenario.scenario_type == ScenarioType.MONITORING.value)
            .where(TravelScenario.is_active.is_(True))
            .where(TravelScenario.deleted_at.is_(None))
            .order_by(TravelScenario.priority, TravelScenario.code)
        ).all()
        active = [item for item in scenarios if item.is_active_on(today)]
        if limit:
            active = active[:limit]

        handle = job_service.create_job(
            session,
            job_type=JobType.MONITORING_BATCH,
            idempotency_key=job_idempotency_key(
                fingerprint="monitoring-batch",
                requested_at=utcnow(),
                bucket_hours=settings.snapshot_interval_hours,
                profile_version=profile_id or "active",
                run_type="BATCH",
            ),
            params={"scenario_count": len(active), "force_refresh": force_refresh},
            created_by="scheduler",
            reuse_terminal=not force_refresh,
        )
        batch_job = handle.job
        if not handle.created and batch_job.is_terminal and not force_refresh:
            return {
                "status": "SKIPPED_IDEMPOTENT",
                "job_id": str(batch_job.id),
                "scenario_count": len(active),
            }
        job_service.transition(
            session,
            batch_job,
            JobStatus.RUNNING,
            message=f"Запуск {len(active)} сценариев мониторинга",
        )
        job_service.set_progress(
            session, batch_job, current=0, total=len(active), message="Диспетчеризация"
        )
        batch_job_id = str(batch_job.id)
        scenario_ids = [str(item.id) for item in active]

    if not scenario_ids:
        with session_scope() as session:
            batch_job = job_service.get_job(session, batch_job_id)
            job_service.transition(
                session,
                batch_job,
                JobStatus.SUCCESS,
                message="Нет активных сценариев мониторинга",
                result={"scenario_count": 0},
            )
        return {"status": "SUCCESS", "scenario_count": 0, "job_id": batch_job_id}

    dispatch = group(
        refresh_monitoring_scenario.s(
            scenario_id, profile_id, force_refresh, batch_job_id
        )
        for scenario_id in scenario_ids
    )
    async_result = dispatch.apply_async()

    with session_scope() as session:
        batch_job = job_service.get_job(session, batch_job_id)
        job_service.transition(
            session,
            batch_job,
            JobStatus.SUCCESS if settings.celery_task_always_eager else JobStatus.RUNNING,
            message=f"Диспетчеризовано {len(scenario_ids)} сценариев",
            result={"scenario_count": len(scenario_ids), "group_id": str(async_result.id)},
        )

    return {
        "status": "DISPATCHED",
        "scenario_count": len(scenario_ids),
        "job_id": batch_job_id,
        "group_id": str(async_result.id),
    }
