"""Calculation Engine: применение методики к Market Snapshot.

Движок принимает готовый снимок рынка и профиль расчета и возвращает
неизменяемый ``ScenarioRun``. Он не обращается к внешним источникам и не
зависит от сети — именно это делает возможным повторный расчет одного
и того же снимка разными методиками (DELTA §1.2).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

from tco.core.enums import (
    AccommodationType,
    ComponentStatus,
    ComponentType,
    ExclusionReason,
    MealType,
    OfferType,
    RailClass,
    RunStatus,
    RunType,
    StarsFilter,
    TransportType,
)
from tco.core.utils import lead_time_days, money, round_display, to_decimal, utcnow
from tco.db.models.offer import Offer
from tco.db.models.run import ScenarioRun
from tco.engine.aggregation import (
    ComponentAggregate,
    SourceCollectionInfo,
    aggregate_component,
)
from tco.engine.confidence import ScenarioConfidence, calculate_scenario_confidence
from tco.engine.explain import build_explainability, build_source_breakdown
from tco.engine.quality import QualityScore, calculate_quality_score
from tco.engine.selection import (
    OutlierReport,
    ScenarioFilterSpec,
    SelectionStats,
    component_of,
    run_selection,
)
from tco.schemas.profile import ProfileRules
from tco.version import ENGINE_VERSION, NORMALIZATION_VERSION


@dataclass(slots=True)
class EngineScenario:
    """Параметры сценария в форме, независимой от ORM."""

    id: Any
    code: str
    name: str
    origin_city_code: str
    destination_city_code: str
    origin_city_name: str
    destination_city_name: str
    departure_date: date
    return_date: date
    adults: int
    children_ages: tuple[int, ...]
    transport_type: TransportType
    accommodation_type: AccommodationType
    stars: StarsFilter
    meal_type: MealType
    cancellation_filter: str
    flight_fare_type: str | None = None
    rail_class: RailClass | None = None

    @property
    def traveler_count(self) -> int:
        return self.adults + len(self.children_ages)

    @property
    def nights(self) -> int:
        return (self.return_date - self.departure_date).days

    def filter_spec(self) -> ScenarioFilterSpec:
        return ScenarioFilterSpec(
            transport_type=self.transport_type,
            flight_fare_type=self.flight_fare_type,
            rail_class=self.rail_class,
            accommodation_type=self.accommodation_type,
            stars=self.stars,
            meal_type=self.meal_type,
            cancellation_filter=self.cancellation_filter,
        )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "code": self.code,
            "name": self.name,
            "origin": self.origin_city_name,
            "origin_code": self.origin_city_code,
            "destination": self.destination_city_name,
            "destination_code": self.destination_city_code,
            "departure_date": self.departure_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "nights": self.nights,
            "adults": self.adults,
            "children_ages": list(self.children_ages),
            "traveler_count": self.traveler_count,
            "transport_type": self.transport_type.value,
            "flight_fare_type": self.flight_fare_type,
            "rail_class": self.rail_class.value if self.rail_class else None,
            "accommodation_type": self.accommodation_type.value,
            "stars": self.stars.value,
            "meal_type": self.meal_type.value,
            "cancellation_filter": self.cancellation_filter,
        }


@dataclass(slots=True)
class EngineInput:
    """Все, что нужно движку для расчета."""

    scenario: EngineScenario
    offers: list[Offer]
    source_infos: dict[str, SourceCollectionInfo]
    rules: ProfileRules
    profile_id: Any
    profile_code: str
    profile_version: str
    market_snapshot_id: Any = None
    snapshot_meta: dict[str, Any] = field(default_factory=dict)
    run_type: RunType = RunType.ON_DEMAND
    observation_date: date | None = None
    connector_stability: dict[str, float] = field(default_factory=dict)
    source_confidence: dict[str, float] = field(default_factory=dict)
    collection_errors: list[dict[str, Any]] = field(default_factory=list)
    cache_key: str | None = None
    created_by: str | None = None
    job_id: Any = None
    served_from_cache: bool = False
    started_at: datetime | None = None
    challenge_verified: bool | None = None


@dataclass(slots=True)
class EngineResult:
    """Результат работы движка."""

    run: ScenarioRun
    offers: list[Offer]
    transport: ComponentAggregate
    accommodation: ComponentAggregate
    quality: QualityScore
    confidence: ScenarioConfidence
    selection: dict[str, SelectionStats]
    outliers: dict[str, OutlierReport]


def calculate_run(payload: EngineInput) -> EngineResult:
    """Полный расчет: отбор → агрегация → качество → уверенность → run."""
    started_at = payload.started_at or utcnow()
    scenario = payload.scenario
    rules = payload.rules
    observation_date = payload.observation_date or started_at.date()

    # --- Отбор ------------------------------------------------------------ #
    transport_offers = [
        offer for offer in payload.offers if component_of(offer) == ComponentType.TRANSPORT
    ]
    accommodation_offers = [
        offer for offer in payload.offers if component_of(offer) == ComponentType.ACCOMMODATION
    ]

    spec = scenario.filter_spec()
    transport_stats, transport_outliers, transport_groups = run_selection(
        transport_offers, spec, rules
    )
    accommodation_stats, accommodation_outliers, accommodation_groups = run_selection(
        accommodation_offers, spec, rules
    )

    # --- Агрегация -------------------------------------------------------- #
    transport_by_source = _group_by_source(transport_offers)
    accommodation_by_source = _group_by_source(accommodation_offers)

    # Транспортный компонент собирают два типа предложений — авиа и ЖД.
    transport_infos = _infos_for(
        payload.source_infos, transport_by_source, (OfferType.FLIGHT, OfferType.RAIL)
    )
    accommodation_infos = _infos_for(
        payload.source_infos, accommodation_by_source, (OfferType.ACCOMMODATION,)
    )

    transport = aggregate_component(
        ComponentType.TRANSPORT, transport_infos, transport_by_source, rules, now=started_at
    )
    accommodation = aggregate_component(
        ComponentType.ACCOMMODATION,
        accommodation_infos,
        accommodation_by_source,
        rules,
        now=started_at,
    )

    _mark_ineligible_sources(transport_offers, transport)
    _mark_ineligible_sources(accommodation_offers, accommodation)

    # --- Итоговая стоимость ------------------------------------------------ #
    totals = _combine_components(transport, accommodation, scenario.traveler_count, rules)

    # --- Качество и уверенность -------------------------------------------- #
    quality = calculate_quality_score(
        transport=transport,
        accommodation=accommodation,
        rules=rules,
        connector_stability=payload.connector_stability,
    )
    confidence = calculate_scenario_confidence(
        quality=quality,
        transport=transport,
        accommodation=accommodation,
        rules=rules,
        source_confidence=payload.source_confidence,
        challenge_verified=payload.challenge_verified,
    )

    # --- Статусы ------------------------------------------------------------ #
    status, component_statuses = _determine_status(
        transport, accommodation, payload.source_infos, payload.offers
    )

    # --- Сборка ScenarioRun -------------------------------------------------- #
    completed_at = utcnow()
    selection = {
        "TRANSPORT": transport_stats,
        "ACCOMMODATION": accommodation_stats,
    }
    outliers = {
        "TRANSPORT": transport_outliers,
        "ACCOMMODATION": accommodation_outliers,
    }
    profile_meta = {
        "id": str(payload.profile_id),
        "code": payload.profile_code,
        "version": payload.profile_version,
        "rules": rules.model_dump(mode="json"),
    }

    explainability = build_explainability(
        scenario_params=scenario.as_dict(),
        profile=profile_meta,
        status=status,
        component_statuses=[item.value for item in component_statuses],
        transport=transport,
        accommodation=accommodation,
        quality=quality,
        confidence=confidence,
        selection=selection,
        outliers=outliers,
        equivalence_groups={
            "TRANSPORT": len(transport_groups),
            "ACCOMMODATION": len(accommodation_groups),
        },
        snapshot={
            **payload.snapshot_meta,
            "market_snapshot_id": str(payload.market_snapshot_id)
            if payload.market_snapshot_id
            else None,
            "served_from_cache": payload.served_from_cache,
        },
        totals=_public_totals(totals),
        errors=payload.collection_errors,
    )

    used_sources = sorted(
        set(transport.eligible_source_codes) | set(accommodation.eligible_source_codes)
    )
    contains_synthetic = transport.contains_synthetic or accommodation.contains_synthetic

    run = ScenarioRun(
        scenario_id=scenario.id,
        market_snapshot_id=payload.market_snapshot_id,
        run_type=payload.run_type.value,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=int((completed_at - started_at).total_seconds() * 1000),
        observation_date=observation_date,
        lead_time_days=lead_time_days(observation_date, scenario.departure_date),
        profile_id=payload.profile_id,
        profile_code=payload.profile_code,
        profile_version=payload.profile_version,
        normalization_version=NORMALIZATION_VERSION,
        engine_version=ENGINE_VERSION,
        status=status.value,
        component_statuses=[item.value for item in component_statuses],
        transport_p25=transport.p25,
        transport_median=transport.median,
        transport_p75=transport.p75,
        transport_source_count=transport.source_count,
        transport_offer_count=transport.offer_count,
        transport_disagreement=transport.disagreement,
        hotel_p25=accommodation.p25,
        hotel_median=accommodation.median,
        hotel_p75=accommodation.p75,
        hotel_source_count=accommodation.source_count,
        hotel_offer_count=accommodation.offer_count,
        hotel_disagreement=accommodation.disagreement,
        total_estimated_cost=totals["total_estimated_cost_decimal"],
        total_p25=totals["total_p25_decimal"],
        total_p75=totals["total_p75_decimal"],
        price_per_person=totals["price_per_person_decimal"],
        transport_share=totals["transport_share"],
        currency=totals["currency"],
        traveler_count=scenario.traveler_count,
        quality_score=quality.score,
        quality_breakdown=quality.as_dict(),
        confidence_level=confidence.level.value,
        confidence_reason=confidence.reason[:1024],
        confidence_factors=confidence.as_dict(),
        source_count=len(used_sources),
        source_codes=used_sources,
        valid_offer_count=transport.offer_count + accommodation.offer_count,
        excluded_offer_count=(
            transport_stats.total
            + accommodation_stats.total
            - transport.offer_count
            - accommodation.offer_count
        ),
        outlier_offer_count=transport_stats.outliers + accommodation_stats.outliers,
        explainability_payload=explainability,
        source_breakdown=build_source_breakdown(transport, accommodation),
        cache_key=payload.cache_key,
        served_from_cache=payload.served_from_cache,
        contains_synthetic_data=contains_synthetic,
        error_summary=payload.collection_errors,
        job_id=payload.job_id,
        created_by=payload.created_by,
        created_at=completed_at,
    )

    return EngineResult(
        run=run,
        offers=payload.offers,
        transport=transport,
        accommodation=accommodation,
        quality=quality,
        confidence=confidence,
        selection=selection,
        outliers=outliers,
    )


# --------------------------------------------------------------------------- #
# Внутренние шаги
# --------------------------------------------------------------------------- #


def _group_by_source(offers: Sequence[Offer]) -> dict[str, list[Offer]]:
    grouped: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        grouped[offer.source_code].append(offer)
    return dict(grouped)


def _infos_for(
    source_infos: dict[str, SourceCollectionInfo],
    offers_by_source: dict[str, list[Offer]],
    offer_types: Sequence[OfferType],
) -> dict[str, SourceCollectionInfo]:
    """Контекст источников, участвовавших в сборе этого компонента.

    Контекст сужается до типов предложений компонента: технический итог
    запроса проживания не должен влиять на допуск транспорта того же
    источника, и наоборот.
    """
    wanted = {item.value for item in offer_types}
    keys = set(offers_by_source)
    return {
        code: info.scoped_to(wanted) for code, info in source_infos.items() if code in keys
    }


def _mark_ineligible_sources(offers: Sequence[Offer], aggregate: ComponentAggregate) -> None:
    """Помечает предложения источников, не допущенных к расчету компонента."""
    ineligible = {
        item.source_code for item in aggregate.source_aggregates if not item.eligible
    }
    if not ineligible:
        return
    reasons = {
        item.source_code: "; ".join(item.ineligibility_reasons)[:512]
        for item in aggregate.source_aggregates
        if not item.eligible
    }
    for offer in offers:
        if (
            offer.source_code in ineligible
            and offer.exclusion_reason == ExclusionReason.NONE.value
        ):
            offer.exclusion_reason = ExclusionReason.SOURCE_NOT_ELIGIBLE.value
            offer.exclusion_detail = reasons.get(offer.source_code)


def _combine_components(
    transport: ComponentAggregate,
    accommodation: ComponentAggregate,
    traveler_count: int,
    rules: ProfileRules,
) -> dict[str, Any]:
    """Складывает компоненты в итоговую расчетную типовую стоимость.

    Итог определяется только при наличии обоих компонентов. Отсутствующий
    компонент никогда не подменяется старым значением (SCOPE-R P §11).
    """
    digits = rules.rounding_digits
    total: Decimal | None = None
    total_p25: Decimal | None = None
    total_p75: Decimal | None = None
    per_person: Decimal | None = None
    transport_share: float | None = None

    if transport.is_available and accommodation.is_available:
        total = money(transport.median + accommodation.median)
        if transport.p25 is not None and accommodation.p25 is not None:
            total_p25 = money(transport.p25 + accommodation.p25)
        if transport.p75 is not None and accommodation.p75 is not None:
            total_p75 = money(transport.p75 + accommodation.p75)
        if traveler_count > 0 and total is not None:
            per_person = money(total / traveler_count)
        if total and total > 0:
            transport_share = float(transport.median / total)

    return {
        "currency": "RUB",
        "transport_median": round_display(transport.median, digits),
        "transport_p25": round_display(transport.p25, digits),
        "transport_p75": round_display(transport.p75, digits),
        "accommodation_median": round_display(accommodation.median, digits),
        "accommodation_p25": round_display(accommodation.p25, digits),
        "accommodation_p75": round_display(accommodation.p75, digits),
        "total_estimated_cost": round_display(total, digits),
        "total_p25": round_display(total_p25, digits),
        "total_p75": round_display(total_p75, digits),
        "price_per_person": round_display(per_person, digits),
        "transport_share": round(transport_share, 4) if transport_share is not None else None,
        "traveler_count": traveler_count,
        "total_estimated_cost_decimal": total,
        "total_p25_decimal": total_p25,
        "total_p75_decimal": total_p75,
        "price_per_person_decimal": per_person,
        "note": (
            "Сумма компонентных медиан не является медианой всех возможных комбинаций "
            "транспорта и проживания."
        ),
    }


def _public_totals(totals: dict[str, Any]) -> dict[str, Any]:
    """Готовит итоги к записи в JSONB: Decimal-поля туда не попадают."""
    return {key: value for key, value in totals.items() if not key.endswith("_decimal")}


def _determine_status(
    transport: ComponentAggregate,
    accommodation: ComponentAggregate,
    source_infos: dict[str, SourceCollectionInfo],
    offers: Sequence[Offer],
) -> tuple[RunStatus, list[ComponentStatus]]:
    """Определяет основной и компонентные статусы расчета."""
    component_statuses: list[ComponentStatus] = []

    if not transport.is_available:
        component_statuses.append(ComponentStatus.PARTIAL_TRANSPORT_MISSING)
    if not accommodation.is_available:
        component_statuses.append(ComponentStatus.PARTIAL_HOTEL_MISSING)
    if transport.is_single_source or accommodation.is_single_source:
        component_statuses.append(ComponentStatus.COMPLETE_SINGLE_SOURCE)

    if transport.is_available and accommodation.is_available:
        return RunStatus.SUCCESS, component_statuses
    if transport.is_available or accommodation.is_available:
        return RunStatus.PARTIAL_SUCCESS, component_statuses

    # Ни одного компонента: различаем технический сбой и отсутствие данных.
    any_success = any(info.outcome.is_ok for info in source_infos.values())
    if source_infos and not any_success:
        return RunStatus.FAILED, component_statuses
    return RunStatus.NO_DATA, component_statuses
