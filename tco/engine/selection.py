"""Отбор предложений: фильтрация по профилю, дедупликация, выбросы.

Порядок шагов зафиксирован методикой (SCOPE-R P §1):

    Validate and Classify → Filter by Calculation Profile →
    Technical Deduplication → Cross-source Equivalence Linking →
    Outlier Marking → Source Eligibility Check

Ни один шаг не удаляет данные физически: предложения остаются в снимке,
меняется только ``exclusion_reason``. Это обязательное условие аудита
и повторного расчета по другой методике.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Sequence

from tco.core.enums import (
    AccommodationType,
    BaggageType,
    CancellationType,
    ComponentType,
    ExclusionReason,
    MealType,
    OfferAttribute,
    OfferType,
    RailClass,
    StarsFilter,
    TransportType,
)
from tco.core.utils import to_decimal
from tco.db.models.offer import Offer
from tco.engine.statistics import iqr_bounds
from tco.normalization.classify import (
    baggage_satisfies,
    cancellation_satisfies,
    meal_satisfies,
    stars_satisfies,
)
from tco.schemas.profile import ProfileRules


@dataclass(slots=True)
class ScenarioFilterSpec:
    """Параметры сценария, по которым фильтруются предложения."""

    transport_type: TransportType
    flight_fare_type: str | None
    rail_class: RailClass | None
    accommodation_type: AccommodationType
    stars: StarsFilter
    meal_type: MealType
    cancellation_filter: str


@dataclass(slots=True)
class SelectionStats:
    """Счетчики по шагам отбора — попадают в explainability."""

    total: int = 0
    invalid: int = 0
    filtered_out: int = 0
    duplicates: int = 0
    outliers: int = 0
    eligible: int = 0
    equivalence_groups: int = 0
    filter_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "invalid": self.invalid,
            "filtered_out": self.filtered_out,
            "duplicates": self.duplicates,
            "outliers": self.outliers,
            "eligible": self.eligible,
            "equivalence_groups": self.equivalence_groups,
            "filter_reasons": dict(sorted(self.filter_reasons.items())),
        }


def unclassified_attributes(offer: Offer) -> frozenset[str]:
    """Признаки предложения, оставшиеся неопределенными.

    Считается по фактическим значениям, а не по ``classification_status``:
    статус хранит только самую значимую проблему, а для разбора «чего именно
    не хватает» нужен полный список.
    """
    found: set[str] = set()

    if offer.offer_type == OfferType.FLIGHT.value:
        flight = offer.flight
        if flight is not None and flight.baggage_type == BaggageType.UNKNOWN.value:
            found.add(OfferAttribute.BAGGAGE.value)
        return frozenset(found)

    if offer.offer_type == OfferType.RAIL.value:
        rail = offer.rail
        if rail is not None and rail.car_type is None:
            found.add(OfferAttribute.RAIL_CLASS.value)
        return frozenset(found)

    accommodation = offer.accommodation
    if accommodation is not None:
        if accommodation.meal_type == MealType.UNKNOWN.value:
            found.add(OfferAttribute.MEAL.value)
        if accommodation.cancellation_type == CancellationType.UNKNOWN.value:
            found.add(OfferAttribute.CANCELLATION.value)
        if not accommodation.capacity_confirmed:
            found.add(OfferAttribute.CAPACITY.value)
    return frozenset(found)


def component_of(offer: Offer) -> ComponentType:
    return (
        ComponentType.ACCOMMODATION
        if offer.offer_type == OfferType.ACCOMMODATION.value
        else ComponentType.TRANSPORT
    )


# --------------------------------------------------------------------------- #
# 1. Фильтрация по профилю и сценарию
# --------------------------------------------------------------------------- #


def profile_filter_reason(
    offer: Offer, spec: ScenarioFilterSpec, rules: ProfileRules
) -> str | None:
    """Возвращает причину отбраковки или ``None``, если предложение подходит."""
    filters = rules.filters

    if offer.offer_type == OfferType.FLIGHT.value:
        if spec.transport_type != TransportType.AVIA:
            return "TRANSPORT_TYPE_MISMATCH"
        flight = offer.flight
        if flight is None:
            return "MISSING_FLIGHT_DETAIL"
        fare_type = spec.flight_fare_type or "CHEAPEST"
        baggage = BaggageType(flight.baggage_type)
        if filters.strict_baggage_classification and not baggage_satisfies(baggage, fare_type):
            return (
                "BAGGAGE_UNCLASSIFIED"
                if baggage == BaggageType.UNKNOWN
                else "BAGGAGE_MISMATCH"
            )
        if filters.max_stops is not None:
            stops = max(flight.outbound_stops or 0, flight.inbound_stops or 0)
            if stops > filters.max_stops:
                return "TOO_MANY_STOPS"
        return None

    if offer.offer_type == OfferType.RAIL.value:
        if spec.transport_type != TransportType.RAIL:
            return "TRANSPORT_TYPE_MISMATCH"
        rail = offer.rail
        if rail is None:
            return "MISSING_RAIL_DETAIL"
        if rail.car_type is None:
            return "RAIL_CLASS_UNCLASSIFIED"
        # Плацкарт и купе агрегируются раздельно.
        if spec.rail_class is not None and rail.car_type != spec.rail_class.value:
            return "RAIL_CLASS_MISMATCH"
        return None

    accommodation = offer.accommodation
    if accommodation is None:
        return "MISSING_ACCOMMODATION_DETAIL"
    if accommodation.accommodation_type != spec.accommodation_type.value:
        return "ACCOMMODATION_TYPE_MISMATCH"
    if not stars_satisfies(
        accommodation.stars,
        accommodation.stars_status == "UNRATED",
        str(spec.stars),
        AccommodationType(accommodation.accommodation_type),
        exact=filters.stars_exact_match,
    ):
        return "STARS_MISMATCH"
    if not meal_satisfies(
        MealType(accommodation.meal_type), spec.meal_type, at_least=filters.meal_at_least_requested
    ):
        return "MEAL_MISMATCH"
    if filters.enforce_cancellation_filter and not cancellation_satisfies(
        CancellationType(accommodation.cancellation_type), spec.cancellation_filter
    ):
        return "CANCELLATION_MISMATCH"
    if filters.require_capacity_confirmation and not accommodation.capacity_confirmed:
        return "CAPACITY_UNCONFIRMED"
    return None


def apply_profile_filter(
    offers: Sequence[Offer], spec: ScenarioFilterSpec, rules: ProfileRules, stats: SelectionStats
) -> None:
    for offer in offers:
        if not offer.is_valid:
            offer.matches_profile = False
            offer.exclusion_reason = ExclusionReason.INVALID.value
            offer.exclusion_detail = "; ".join(offer.validation_messages or [])[:512] or None
            stats.invalid += 1
            continue
        reason = profile_filter_reason(offer, spec, rules)
        if reason:
            offer.matches_profile = False
            offer.exclusion_reason = ExclusionReason.PROFILE_FILTER.value
            offer.exclusion_detail = reason
            stats.filtered_out += 1
            stats.filter_reasons[reason] = stats.filter_reasons.get(reason, 0) + 1
        else:
            offer.matches_profile = True


# --------------------------------------------------------------------------- #
# 2. Техническая дедупликация
# --------------------------------------------------------------------------- #


def deduplicate(offers: Sequence[Offer], stats: SelectionStats) -> None:
    """Технический дубликат удаляется из расчетной выборки.

    Дубликатом считается совпадение отпечатка в пределах одного источника:
    один и тот же вариант, возвращенный дважды. Совпадения между источниками
    дубликатами не являются — они связываются группой эквивалентности.
    """
    seen: dict[tuple[str, str], Offer] = {}
    for offer in offers:
        if offer.exclusion_reason != ExclusionReason.NONE.value:
            continue
        key = (offer.source_code, offer.technical_fingerprint)
        if key in seen:
            offer.is_duplicate = True
            offer.exclusion_reason = ExclusionReason.TECHNICAL_DUPLICATE.value
            offer.exclusion_detail = f"Дубликат предложения {seen[key].source_offer_id or '—'}"
            stats.duplicates += 1
        else:
            seen[key] = offer


def link_equivalence_groups(offers: Sequence[Offer], stats: SelectionStats) -> dict[str, list[Offer]]:
    """Связывает сопоставимые предложения разных источников общей группой.

    Группа не удаляет предложения — она позволяет сравнивать источники на
    одном и том же рыночном варианте (SCOPE-R P §6).
    """
    groups: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        if offer.equivalence_key:
            groups[offer.equivalence_key].append(offer)
    cross_source = {
        key: items
        for key, items in groups.items()
        if len({item.source_code for item in items}) > 1
    }
    stats.equivalence_groups = len(cross_source)
    return cross_source


# --------------------------------------------------------------------------- #
# 3. Выбросы
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OutlierReport:
    scope: str
    bounds: dict[str, dict[str, float]] = field(default_factory=dict)
    marked: int = 0
    skipped_samples: list[str] = field(default_factory=list)
    reverted_samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "bounds": self.bounds,
            "marked": self.marked,
            "skipped_samples": self.skipped_samples,
            "reverted_samples": self.reverted_samples,
        }


def mark_outliers(
    offers: Sequence[Offer], rules: ProfileRules, stats: SelectionStats
) -> OutlierReport:
    """Маркирует выбросы методом IQR.

    Выбросы сохраняются в снимке, маркируются и исключаются из агрегата,
    но остаются видимыми в диагностике (SCOPE-R P §7).
    """
    outlier_rules = rules.outliers
    report = OutlierReport(scope=outlier_rules.scope)
    if outlier_rules.method == "NONE":
        return report

    candidates = [
        offer for offer in offers if offer.exclusion_reason == ExclusionReason.NONE.value
    ]
    if not candidates:
        return report

    buckets: dict[str, list[Offer]] = defaultdict(list)
    for offer in candidates:
        component = component_of(offer).value
        key = component if outlier_rules.scope == "GLOBAL" else f"{component}|{offer.source_code}"
        buckets[key].append(offer)

    method = rules.aggregation.percentile_method
    for key, bucket in sorted(buckets.items()):
        if len(bucket) < outlier_rules.min_sample_size:
            report.skipped_samples.append(f"{key} (n={len(bucket)})")
            continue
        prices = [to_decimal(offer.total_price) for offer in bucket]
        bounds = iqr_bounds(
            [price for price in prices if price is not None],
            multiplier=outlier_rules.iqr_multiplier,
            method=method,
        )
        if bounds is None:
            continue

        flagged = [
            offer
            for offer, price in zip(bucket, prices)
            if price is not None and (float(price) < bounds.lower or float(price) > bounds.upper)
        ]
        # Предохранитель: правило, отбраковывающее слишком много, скорее всего
        # применено к неоднородной выборке — тогда маркировка отменяется.
        if flagged and len(flagged) / len(bucket) > outlier_rules.max_outlier_ratio:
            report.reverted_samples.append(
                f"{key} ({len(flagged)}/{len(bucket)} > {outlier_rules.max_outlier_ratio:.0%})"
            )
            continue

        report.bounds[key] = {
            "q1": round(bounds.q1, 2),
            "q3": round(bounds.q3, 2),
            "iqr": round(bounds.iqr, 2),
            "lower": round(bounds.lower, 2),
            "upper": round(bounds.upper, 2),
            "sample_size": len(bucket),
        }
        for offer in flagged:
            offer.is_outlier = True
            offer.exclusion_reason = ExclusionReason.OUTLIER.value
            offer.exclusion_detail = (
                f"Вне границ IQR [{bounds.lower:.0f}; {bounds.upper:.0f}]"
            )
            report.marked += 1
            stats.outliers += 1

    return report


# --------------------------------------------------------------------------- #
# Итог отбора
# --------------------------------------------------------------------------- #


def countable(offers: Iterable[Offer]) -> list[Offer]:
    """Предложения, участвующие в агрегированном расчете."""
    return [offer for offer in offers if offer.exclusion_reason == ExclusionReason.NONE.value]


def prices_of(offers: Iterable[Offer]) -> list[Decimal]:
    values = [to_decimal(offer.total_price) for offer in offers]
    return [value for value in values if value is not None]


def run_selection(
    offers: Sequence[Offer],
    spec: ScenarioFilterSpec,
    rules: ProfileRules,
) -> tuple[SelectionStats, OutlierReport, dict[str, list[Offer]]]:
    """Полный конвейер отбора над предложениями снимка."""
    stats = SelectionStats(total=len(offers))
    apply_profile_filter(offers, spec, rules, stats)
    deduplicate(offers, stats)
    equivalence = link_equivalence_groups(
        [offer for offer in offers if offer.exclusion_reason == ExclusionReason.NONE.value], stats
    )
    outlier_report = mark_outliers(offers, rules, stats)
    stats.eligible = len(countable(offers))
    return stats, outlier_report, equivalence
