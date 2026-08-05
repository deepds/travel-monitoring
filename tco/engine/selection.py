"""Отбор предложений: фильтрация по профилю, дедупликация, выбросы.

Порядок шагов зафиксирован методикой (SCOPE-R P §1):

    Validate and Classify → Filter by Calculation Profile →
    Technical Deduplication → Fare Variant Collapsing →
    Cross-source Equivalence Linking → Outlier Marking →
    Source Eligibility Check

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


#: Обозначения эконом-класса у источников. Все остальное — бизнес, комфорт и
#: первый класс — сценариями не наблюдается.
ECONOMY_CABINS = frozenset({"ECONOMIC", "ECONOMY", "ЭКОНОМ"})


@dataclass(slots=True)
class ScenarioFilterSpec:
    """Параметры сценария, по которым фильтруются предложения.

    ``None`` в типе транспорта или размещения означает, что компонента не
    наблюдается: предложения такого рода до фильтра доходить не должны, а если
    дошли — отбраковываются как несопоставимые.
    """

    transport_type: TransportType | None
    flight_fare_type: str | None
    rail_class: RailClass | None
    accommodation_type: AccommodationType | None
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
    fare_variants: int = 0
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
            "fare_variants": self.fare_variants,
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
        if spec.transport_type is None:
            return "COMPONENT_NOT_OBSERVED"
        if spec.transport_type != TransportType.AVIA:
            return "TRANSPORT_TYPE_MISMATCH"
        flight = offer.flight
        if flight is None:
            return "MISSING_FLIGHT_DETAIL"
        # Все тарифные режимы сценария (CHEAPEST, CABIN_BAGGAGE,
        # CHECKED_BAGGAGE) описывают эконом-класс: бизнес-тарифом ни один из
        # них не удовлетворяется. Без этой проверки бизнес попадал в выборку
        # наравне с экономом и поднимал медиану — на живых данных бизнес
        # составлял треть зачтенных предложений. Неизвестный класс не
        # отбраковывается: часть источников его не сообщает.
        if flight.cabin_class and flight.cabin_class.upper() not in ECONOMY_CABINS:
            return "CABIN_CLASS_MISMATCH"
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
        if spec.transport_type is None:
            return "COMPONENT_NOT_OBSERVED"
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

    if spec.accommodation_type is None:
        return "COMPONENT_NOT_OBSERVED"
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


def _itinerary_key(flight) -> tuple:  # noqa: ANN001
    """Идентичность рейса без тарифа: те же борта в те же времена.

    Номера рейсов вместе с временами вылета различают рейс надежнее, чем
    номера сами по себе: один и тот же номер выполняется ежедневно.
    """
    numbers = tuple(str(number) for number in (flight.flight_numbers or []))
    carriers = tuple(str(carrier) for carrier in (flight.marketing_carriers or []))
    return (
        numbers or carriers,
        str(flight.outbound_departure_at),
        str(flight.inbound_departure_at),
    )


def collapse_fare_variants(offers: Sequence[Offer], stats: SelectionStats) -> None:
    """Из тарифных вариантов одного рейса в расчет идет один — самый дешевый.

    Источник отдает на рейс несколько тарифов: «Эконом Базовый», «Оптимум»,
    «Максимум» и так далее. Это не дубликаты — у них разные условия, и на
    анализе надбавок они нужны все. Но в стоимости поездки рейс должен
    участвовать один раз: иначе медиана измеряет структуру тарифной сетки, а
    не цену, которую платит покупатель. На живых данных рейсы считались по
    4–6 раз, и медиана оказывалась выше реальной цены поездки в полтора раза.

    Схлопывание идет внутри источника: одинаковый рейс у разных источников —
    это межисточниковое сравнение, им занимается группировка эквивалентности.

    Какой тариф останется, определяет фильтр профиля выше: сюда доходят
    только варианты, удовлетворяющие тарифному режиму сценария, и из них
    выбирается самый дешевый.
    """
    groups: dict[tuple, list[Offer]] = defaultdict(list)
    for offer in offers:
        if offer.exclusion_reason != ExclusionReason.NONE.value:
            continue
        if offer.offer_type != OfferType.FLIGHT.value:
            continue
        flight = offer.flight
        if flight is None:
            continue
        groups[(offer.source_code, _itinerary_key(flight))].append(offer)

    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (to_decimal(item.total_price) or Decimal("Infinity")))
        keeper = group[0]
        for offer in group[1:]:
            offer.exclusion_reason = ExclusionReason.FARE_VARIANT.value
            offer.exclusion_detail = (
                f"Более дорогой тариф того же рейса; в расчет взят "
                f"{keeper.flight.fare_family or '—'} за {keeper.total_price}"
            )[:512]
            stats.fare_variants += 1


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


def _outside_bounds(price: float, bounds) -> bool:  # noqa: ANN001
    """Лежит ли цена за границами IQR — с допуском на погрешность вычислений.

    Цена, попавшая ровно на границу, выбросом не является. Сравнивать ее с
    границей напрямую нельзя: граница получается арифметикой над числами с
    плавающей точкой и промахивается на единицы последнего разряда. На живых
    данных этого хватало, чтобы потерять самое дешевое предложение: плацкарт
    Калининград — Москва стоил 13753.2, нижняя граница вычислялась как
    13753.200000000004, и самый дешевый вариант поездки отбраковывался как
    выброс — медиана вырастала с 13753 до 17191 рублей.
    """
    tolerance = max(abs(bounds.lower), abs(bounds.upper), 1.0) * 1e-9
    return price < bounds.lower - tolerance or price > bounds.upper + tolerance


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
            if price is not None and _outside_bounds(float(price), bounds)
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
    # После дедупликации и до выбросов: границы выброса считаются по той же
    # выборке, что идет в расчет, иначе их задавали бы дорогие тарифы,
    # которые в расчет все равно не попадут.
    collapse_fare_variants(offers, stats)
    equivalence = link_equivalence_groups(
        [offer for offer in offers if offer.exclusion_reason == ExclusionReason.NONE.value], stats
    )
    outlier_report = mark_outliers(offers, rules, stats)
    stats.eligible = len(countable(offers))
    return stats, outlier_report, equivalence
