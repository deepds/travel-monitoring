"""Оркестрация расчета: кэш → снимок → движок → ScenarioRun.

Реализует основной pipeline SCOPE-R P §1 и правило DELTA §2.2: если результат
взят из кэша, внешний сбор не выполняется, а расчет ссылается на существующий
``MarketSnapshot``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from tco.core.config import Settings, get_settings
from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    ConnectorOutcome,
    FlightFareType,
    MealType,
    RailClass,
    RunStatus,
    RunType,
    SnapshotType,
    SourceCategory,
    StarsFilter,
    TransportType,
)
from tco.core.errors import NotFoundError, ScenarioValidationError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.cache.result_cache import ResultCache, get_result_cache
from tco.db.models.offer import Offer
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot, SnapshotSourceResult
from tco.db.models.source import Source, SourceConfidence, SourceMetric
from tco.engine.aggregation import SourceCollectionInfo
from tco.engine.fingerprint import ScenarioKey, cache_key as build_cache_key
from tco.engine.pipeline import EngineInput, EngineResult, EngineScenario, calculate_run
from tco.engine.validation import (
    CityCapability,
    HorizonInfo,
    ScenarioInput,
    ValidationResult,
    validate_scenario,
)
from tco.schemas.profile import ProfileRules
from tco.services.snapshot_builder import build_snapshot
from tco.storage.raw_store import RawStore

logger = get_logger(__name__)


@dataclass(slots=True)
class CalculationOutcome:
    """Итог расчета для API и UI."""

    run: ScenarioRun | None
    snapshot: MarketSnapshot | None
    from_cache: bool = False
    snapshot_created: bool = False
    validation: ValidationResult | None = None

    @property
    def status(self) -> str:
        if self.run is not None:
            return self.run.status
        return RunStatus.UNSUPPORTED.value


# --------------------------------------------------------------------------- #
# Подготовка контекста
# --------------------------------------------------------------------------- #


def scenario_key(scenario: TravelScenario) -> ScenarioKey:
    return ScenarioKey(
        origin_city_code=scenario.origin_city.code,
        destination_city_code=scenario.destination_city.code,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        adults=scenario.adults,
        children_ages=tuple(scenario.children_ages or []),
        transport_type=TransportType(scenario.transport_type),
        flight_fare_type=FlightFareType(scenario.flight_fare_type)
        if scenario.flight_fare_type
        else None,
        rail_class=RailClass(scenario.rail_class) if scenario.rail_class else None,
        accommodation_type=AccommodationType(scenario.accommodation_type),
        stars=StarsFilter(scenario.stars),
        meal_type=MealType(scenario.meal_type),
        cancellation_filter=CancellationFilter(scenario.cancellation_filter),
    )


def engine_scenario(scenario: TravelScenario) -> EngineScenario:
    return EngineScenario(
        id=scenario.id,
        code=scenario.code,
        name=scenario.name,
        origin_city_code=scenario.origin_city.code,
        destination_city_code=scenario.destination_city.code,
        origin_city_name=scenario.origin_city.name,
        destination_city_name=scenario.destination_city.name,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        adults=scenario.adults,
        children_ages=tuple(scenario.children_ages or []),
        transport_type=TransportType(scenario.transport_type),
        accommodation_type=AccommodationType(scenario.accommodation_type),
        stars=StarsFilter(scenario.stars),
        meal_type=MealType(scenario.meal_type),
        cancellation_filter=scenario.cancellation_filter,
        flight_fare_type=scenario.flight_fare_type,
        rail_class=RailClass(scenario.rail_class) if scenario.rail_class else None,
    )


def active_profile(session: Session, scenario: TravelScenario | None = None) -> CalculationProfile:
    """Профиль сценария, иначе — единственный активный профиль по умолчанию."""
    from tco.core.enums import ProfileStatus

    if scenario is not None and scenario.calculation_profile_id:
        profile = session.get(CalculationProfile, scenario.calculation_profile_id)
        if profile is not None and profile.is_active:
            return profile

    profile = session.scalars(
        select(CalculationProfile)
        .where(CalculationProfile.status == ProfileStatus.ACTIVE.value)
        .order_by(CalculationProfile.activated_at.desc())
    ).first()
    if profile is None:
        raise NotFoundError("Нет активного профиля расчета")
    return profile


def resolve_horizon(session: Session, *, allow_synthetic: bool = False) -> HorizonInfo:
    """Доступный горизонт по активным источникам (SCOPE-R C §7)."""
    settings = get_settings()
    sources = session.scalars(select(Source)).all()
    horizon = HorizonInfo()

    for source in sources:
        if not source.is_usable:
            continue
        if source.is_synthetic and not (allow_synthetic or settings.sandbox_sources_enabled):
            continue
        if source.category == SourceCategory.ACCOMMODATION.value or source.supports_offer_type(
            "ACCOMMODATION"
        ):
            horizon.accommodation_sources.append(source.code)
            horizon.accommodation_min_date = _min_date(
                horizon.accommodation_min_date, source.min_supported_date
            )
            horizon.accommodation_max_date = _max_date(
                horizon.accommodation_max_date, source.max_supported_date
            )
        if source.supports_offer_type("FLIGHT") or source.supports_offer_type("RAIL"):
            horizon.transport_sources.append(source.code)
            horizon.transport_min_date = _min_date(
                horizon.transport_min_date, source.min_supported_date
            )
            horizon.transport_max_date = _max_date(
                horizon.transport_max_date, source.max_supported_date
            )

    horizon.transport_sources = sorted(set(horizon.transport_sources))
    horizon.accommodation_sources = sorted(set(horizon.accommodation_sources))
    return horizon


def _min_date(current: date | None, candidate: date | None) -> date | None:
    """Горизонт объединяется, а не пересекается: достаточно одного источника."""
    if candidate is None:
        return None if current is None else current
    return candidate if current is None else min(current, candidate)


def _max_date(current: date | None, candidate: date | None) -> date | None:
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)


def city_capabilities(session: Session) -> dict[str, CityCapability]:
    cities = session.scalars(select(City)).all()
    return {
        city.code: CityCapability(
            code=city.code,
            name=city.name,
            is_active=city.is_active,
            supports_avia=city.supports_avia,
            supports_rail=city.supports_rail,
        )
        for city in cities
    }


def validate(
    session: Session, scenario: TravelScenario, profile: CalculationProfile | None = None
) -> ValidationResult:
    """Валидация сценария до любых внешних запросов."""
    rules = ProfileRules.parse(profile.rules if profile else None)
    scenario_input = ScenarioInput(
        origin_city_code=scenario.origin_city.code,
        destination_city_code=scenario.destination_city.code,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        adults=scenario.adults,
        children_ages=tuple(scenario.children_ages or []),
        transport_type=TransportType(scenario.transport_type),
        accommodation_type=AccommodationType(scenario.accommodation_type),
        stars=StarsFilter(scenario.stars),
        meal_type=MealType(scenario.meal_type),
        cancellation_filter=CancellationFilter(scenario.cancellation_filter),
        flight_fare_type=FlightFareType(scenario.flight_fare_type)
        if scenario.flight_fare_type
        else None,
        rail_class=RailClass(scenario.rail_class) if scenario.rail_class else None,
    )
    return validate_scenario(
        scenario_input,
        cities=city_capabilities(session),
        horizon=resolve_horizon(
            session, allow_synthetic=rules.filters.allow_synthetic_sources
        ),
        profile_active=profile.is_active if profile else True,
    )


# --------------------------------------------------------------------------- #
# Исторические метрики источников
# --------------------------------------------------------------------------- #


def connector_stability(session: Session, window_days: int = 30) -> dict[str, float]:
    """Доля успешных обращений по каждому источнику за окно."""
    since = utcnow() - timedelta(days=window_days)
    rows = session.execute(
        select(
            Source.code,
            func.count(SourceMetric.id),
            func.sum(
                case(
                    (
                        SourceMetric.outcome.in_(
                            [ConnectorOutcome.SUCCESS.value, ConnectorOutcome.EMPTY.value]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(SourceMetric, SourceMetric.source_id == Source.id)
        .where(SourceMetric.observed_at >= since)
        .group_by(Source.code)
    ).all()
    return {
        code: (float(success or 0) / total)
        for code, total, success in rows
        if total and total > 0
    }


def source_confidence_map(session: Session) -> dict[str, float]:
    """Актуальный Source Confidence по каждому источнику."""
    latest_date = session.scalar(select(func.max(SourceConfidence.calculation_date)))
    if latest_date is None:
        return {}
    rows = session.execute(
        select(Source.code, SourceConfidence.score, SourceConfidence.manual_override)
        .join(SourceConfidence, SourceConfidence.source_id == Source.id)
        .where(SourceConfidence.calculation_date == latest_date)
    ).all()
    return {
        code: float(override if override is not None else score) for code, score, override in rows
    }


# --------------------------------------------------------------------------- #
# Расчет по существующему снимку
# --------------------------------------------------------------------------- #


def build_source_infos(
    session: Session, snapshot: MarketSnapshot
) -> dict[str, SourceCollectionInfo]:
    """Собирает технический контекст источников снимка."""
    rows = session.scalars(
        select(SnapshotSourceResult).where(SnapshotSourceResult.market_snapshot_id == snapshot.id)
    ).all()
    confidence = source_confidence_map(session)

    unreported = unreported_attributes_map(session)

    infos: dict[str, SourceCollectionInfo] = {}
    for row in rows:
        existing = infos.get(row.source_code)
        info = SourceCollectionInfo(
            source_code=row.source_code,
            source_name=row.source_name,
            outcome=ConnectorOutcome(row.outcome),
            collected_at=row.collected_at,
            is_synthetic=row.is_synthetic,
            latency_ms=row.latency_ms,
            raw_offer_count=row.raw_offer_count,
            normalized_offer_count=row.normalized_offer_count,
            invalid_offer_count=row.invalid_offer_count,
            unclassified_offer_count=row.unclassified_offer_count,
            duplicate_offer_count=row.duplicate_offer_count,
            confidence_score=confidence.get(row.source_code),
            error_code=row.error_code,
            error_message=row.error_message,
            unreported_attributes=unreported.get(row.source_code, frozenset()),
        )
        # Один источник может отдавать и транспорт, и проживание. Сводный
        # объект хранит худший исход и суммарные счетчики — он нужен для
        # статуса расчета; разрез по типам сохраняется рядом, потому что
        # допуск к компоненту проверяется отдельно (см. ``scoped_to``).
        if existing is None:
            existing = replace(info, by_offer_type={})
            infos[row.source_code] = existing
        else:
            existing.raw_offer_count += info.raw_offer_count
            existing.normalized_offer_count += info.normalized_offer_count
            existing.invalid_offer_count += info.invalid_offer_count
            existing.unclassified_offer_count += info.unclassified_offer_count
            if existing.outcome.is_ok and not info.outcome.is_ok:
                existing.outcome = info.outcome
        existing.by_offer_type[row.offer_type] = info
    return infos


def unreported_attributes_map(session: Session) -> dict[str, frozenset[str]]:
    """Признаки, которые источники структурно не сообщают.

    Объявляются в реестре источников: это свойство контракта источника, а не
    результат конкретного сбора.
    """
    rows = session.execute(select(Source.code, Source.unreported_attributes)).all()
    return {code: frozenset(values or ()) for code, values in rows}


def calculate_from_snapshot(
    session: Session,
    snapshot: MarketSnapshot,
    profile: CalculationProfile,
    *,
    run_type: RunType = RunType.ON_DEMAND,
    created_by: str | None = None,
    job_id: uuid.UUID | None = None,
    cache_key: str | None = None,
    served_from_cache: bool = False,
    persist: bool = True,
) -> EngineResult:
    """Применяет методику к снимку и создает ``ScenarioRun``.

    Снимок не изменяется — можно рассчитать его повторно любым числом
    профилей (DELTA §6.8 ``recalculate``).
    """
    scenario = snapshot.scenario or session.get(TravelScenario, snapshot.scenario_id)
    if scenario is None:
        raise NotFoundError("Сценарий снимка не найден")

    rules = ProfileRules.parse(profile.rules)
    offers = list(
        session.scalars(select(Offer).where(Offer.market_snapshot_id == snapshot.id)).all()
    )

    payload = EngineInput(
        scenario=engine_scenario(scenario),
        offers=offers,
        source_infos=build_source_infos(session, snapshot),
        rules=rules,
        profile_id=profile.id,
        profile_code=profile.code,
        profile_version=profile.version,
        market_snapshot_id=snapshot.id,
        snapshot_meta={
            "snapshot_type": snapshot.snapshot_type,
            "observed_at": snapshot.observed_at.isoformat(),
            "observation_date": snapshot.observation_date.isoformat(),
            "observation_slot": snapshot.observation_slot,
            "status": snapshot.status,
            "source_codes": list(snapshot.source_codes or []),
            "raw_response_count": len(snapshot.raw_response_refs or []),
            "html_snapshot_count": len(snapshot.html_snapshot_refs or []),
            "contains_synthetic_data": snapshot.contains_synthetic_data,
        },
        run_type=run_type,
        observation_date=snapshot.observation_date,
        connector_stability=connector_stability(session, rules.quality.stability_window_days),
        source_confidence=source_confidence_map(session),
        collection_errors=list(snapshot.error_summary or []),
        cache_key=cache_key,
        created_by=created_by,
        job_id=job_id,
        served_from_cache=served_from_cache,
    )

    result = calculate_run(payload)
    if persist:
        session.add(result.run)
        session.flush()
    return result


# --------------------------------------------------------------------------- #
# Полный расчет сценария
# --------------------------------------------------------------------------- #


def calculate_scenario(
    session: Session,
    scenario: TravelScenario,
    *,
    profile: CalculationProfile | None = None,
    run_type: RunType = RunType.ON_DEMAND,
    snapshot_type: SnapshotType | None = None,
    force_refresh: bool = False,
    use_cache: bool = True,
    created_by: str | None = None,
    job_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    cache: ResultCache | None = None,
    raw_store: RawStore | None = None,
    skip_validation: bool = False,
) -> CalculationOutcome:
    """Полный расчет: валидация → кэш → снимок → движок → кэш."""
    settings = settings or get_settings()
    cache = cache or get_result_cache()
    profile = profile or active_profile(session, scenario)
    rules = ProfileRules.parse(profile.rules)

    # --- Валидация до внешних запросов ------------------------------------ #
    if not skip_validation:
        validation = validate(session, scenario, profile)
        if not validation.is_valid:
            logger.info(
                "Сценарий не прошел валидацию — внешние запросы не выполняются",
                scenario=scenario.code,
                errors=[issue.code for issue in validation.errors],
            )
            return CalculationOutcome(run=None, snapshot=None, validation=validation)
    else:
        validation = None

    key = build_cache_key(scenario_key(scenario), profile.version, profile.code)

    # --- Кэш ---------------------------------------------------------------- #
    if use_cache and not force_refresh:
        hit = cache.get(session, key)
        if hit is not None and hit.scenario_run_id:
            run = session.get(ScenarioRun, uuid.UUID(hit.scenario_run_id))
            if run is not None:
                logger.info(
                    "Результат отдан из кэша",
                    scenario=scenario.code,
                    layer=hit.layer,
                    run_id=str(run.id),
                )
                snapshot = (
                    session.get(MarketSnapshot, uuid.UUID(hit.market_snapshot_id))
                    if hit.market_snapshot_id
                    else None
                )
                return CalculationOutcome(
                    run=run, snapshot=snapshot, from_cache=True, validation=validation
                )

    # --- Снимок ------------------------------------------------------------- #
    snapshot_type = snapshot_type or (
        SnapshotType.DAILY_MONITORING if run_type == RunType.MONITORING else SnapshotType.ON_DEMAND
    )
    build = build_snapshot(
        session,
        scenario,
        rules=rules,
        snapshot_type=snapshot_type,
        force_refresh=force_refresh,
        created_by=created_by,
        job_id=job_id,
        settings=settings,
        raw_store=raw_store,
    )

    # --- Расчет -------------------------------------------------------------- #
    result = calculate_from_snapshot(
        session,
        build.snapshot,
        profile,
        run_type=run_type,
        created_by=created_by,
        job_id=job_id,
        cache_key=key,
    )

    # --- Обновление кэша ------------------------------------------------------ #
    if use_cache:
        cache.put(
            session,
            cache_key=key,
            scenario_fingerprint=scenario.fingerprint,
            profile_version=profile.version,
            scenario_run_id=result.run.id,
            market_snapshot_id=build.snapshot.id,
            payload={
                "status": result.run.status,
                "total_estimated_cost": float(result.run.total_estimated_cost)
                if result.run.total_estimated_cost is not None
                else None,
                "quality_score": result.run.quality_score,
                "confidence_level": result.run.confidence_level,
            },
            ttl_minutes=rules.limits.result_cache_ttl_minutes,
        )

    return CalculationOutcome(
        run=result.run,
        snapshot=build.snapshot,
        from_cache=False,
        snapshot_created=build.created,
        validation=validation,
    )
