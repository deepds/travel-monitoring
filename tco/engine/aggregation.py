"""Допуск источников и агрегация (SCOPE-R P §8–§10).

Порядок строго двухступенчатый: сначала рассчитывается статистика внутри
каждого пригодного источника, затем источники объединяются между собой.
Сумма компонентных медиан не трактуется как медиана всех возможных
комбинаций — это прямо оговорено в методике.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Collection, Sequence

from tco.core.enums import ComponentType, ConnectorOutcome, ExclusionReason, OfferType
from tco.core.utils import minutes_between, to_decimal, utcnow
from tco.db.models.offer import Offer
from tco.engine.selection import unclassified_attributes
from tco.engine.statistics import (
    Distribution,
    describe,
    mean,
    median,
    percentile,
    relative_disagreement,
    trimmed_mean,
    winsorized_mean,
)
from tco.schemas.profile import ProfileRules


@dataclass(slots=True)
class SourceCollectionInfo:
    """Технический контекст источника, нужный для проверки допуска."""

    source_code: str
    source_name: str
    outcome: ConnectorOutcome
    collected_at: datetime
    is_synthetic: bool = False
    latency_ms: int | None = None
    raw_offer_count: int = 0
    normalized_offer_count: int = 0
    invalid_offer_count: int = 0
    unclassified_offer_count: int = 0
    duplicate_offer_count: int = 0
    confidence_score: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    #: Признаки, которые источник структурно не сообщает (``MEAL`` и т. п.).
    unreported_attributes: frozenset[str] = frozenset()
    #: Разрез по типам предложений. Один источник поставляет и транспорт, и
    #: проживание, а допуск проверяется для каждого компонента отдельно.
    by_offer_type: dict[str, "SourceCollectionInfo"] = field(default_factory=dict)

    def scoped_to(self, offer_types: Collection[str]) -> "SourceCollectionInfo":
        """Контекст, ограниченный типами предложений одного компонента.

        Неудачный запрос одного типа не должен снимать источник с допуска по
        другому: иначе сломанный поиск проживания обнуляет успешно собранные
        авиапредложения того же источника.
        """
        parts = [info for kind, info in self.by_offer_type.items() if kind in offer_types]
        if not parts:
            return self

        failed = [info for info in parts if not info.outcome.is_ok]
        leading = failed[0] if failed else parts[0]
        return replace(
            self,
            outcome=leading.outcome,
            # Возраст данных считается по самой старой части компонента.
            collected_at=min(info.collected_at for info in parts),
            latency_ms=max((info.latency_ms or 0) for info in parts) or None,
            raw_offer_count=sum(info.raw_offer_count for info in parts),
            normalized_offer_count=sum(info.normalized_offer_count for info in parts),
            invalid_offer_count=sum(info.invalid_offer_count for info in parts),
            unclassified_offer_count=sum(info.unclassified_offer_count for info in parts),
            duplicate_offer_count=sum(info.duplicate_offer_count for info in parts),
            error_code=leading.error_code,
            error_message=leading.error_message,
            by_offer_type={},
        )


@dataclass(slots=True)
class SourceAggregate:
    """Результат агрегации внутри одного источника."""

    source_code: str
    source_name: str
    component: ComponentType
    distribution: Distribution
    statistic: float | None
    eligible: bool
    ineligibility_reasons: list[str] = field(default_factory=list)
    offer_count: int = 0
    valid_offer_count: int = 0
    invalid_offer_count: int = 0
    unclassified_offer_count: int = 0
    duplicate_offer_count: int = 0
    outlier_offer_count: int = 0
    excluded_offer_count: int = 0
    data_age_minutes: float | None = None
    latency_ms: int | None = None
    is_synthetic: bool = False
    confidence_score: float | None = None
    outcome: str = ConnectorOutcome.SUCCESS.value

    def as_dict(self) -> dict:
        return {
            "source_code": self.source_code,
            "source_name": self.source_name,
            "component": self.component.value,
            "eligible": self.eligible,
            "ineligibility_reasons": self.ineligibility_reasons,
            "statistic": _round(self.statistic),
            "distribution": {
                key: _round(value) if isinstance(value, float) else value
                for key, value in self.distribution.as_dict().items()
            },
            "offer_count": self.offer_count,
            "valid_offer_count": self.valid_offer_count,
            "invalid_offer_count": self.invalid_offer_count,
            "unclassified_offer_count": self.unclassified_offer_count,
            "duplicate_offer_count": self.duplicate_offer_count,
            "outlier_offer_count": self.outlier_offer_count,
            "excluded_offer_count": self.excluded_offer_count,
            "data_age_minutes": _round(self.data_age_minutes, 1),
            "latency_ms": self.latency_ms,
            "is_synthetic": self.is_synthetic,
            "confidence_score": _round(self.confidence_score, 1),
            "outcome": self.outcome,
        }


@dataclass(slots=True)
class ComponentAggregate:
    """Итог по компоненту стоимости после межисточниковой агрегации."""

    component: ComponentType
    p25: Decimal | None
    median: Decimal | None
    p75: Decimal | None
    source_aggregates: list[SourceAggregate]
    eligible_source_codes: list[str]
    disagreement: float | None
    is_single_source: bool
    method: str
    offer_count: int
    excluded_offer_count: int
    outlier_offer_count: int
    unclassified_offer_count: int
    contains_synthetic: bool = False
    #: Границы наблюдавшихся цен. В отличие от перцентилей это не устойчивая
    #: оценка, а фактический размах выборки: самое дешевое и самое дорогое
    #: допущенное предложение среди всех пригодных источников.
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    @property
    def is_available(self) -> bool:
        return self.median is not None

    @property
    def source_count(self) -> int:
        return len(self.eligible_source_codes)

    def as_dict(self) -> dict:
        return {
            "component": self.component.value,
            "available": self.is_available,
            "min": _decimal_out(self.minimum),
            "p25": _decimal_out(self.p25),
            "median": _decimal_out(self.median),
            "p75": _decimal_out(self.p75),
            "max": _decimal_out(self.maximum),
            "source_count": self.source_count,
            "eligible_sources": self.eligible_source_codes,
            "disagreement": _round(self.disagreement, 4),
            "single_source": self.is_single_source,
            "method": self.method,
            "offer_count": self.offer_count,
            "excluded_offer_count": self.excluded_offer_count,
            "outlier_offer_count": self.outlier_offer_count,
            "unclassified_offer_count": self.unclassified_offer_count,
            "contains_synthetic": self.contains_synthetic,
            "sources": [item.as_dict() for item in self.source_aggregates],
        }


# --------------------------------------------------------------------------- #
# Допуск источника
# --------------------------------------------------------------------------- #


def _min_offers_after_dedup(eligibility, offers: Sequence[Offer]) -> int:  # noqa: ANN001
    """Порог по числу предложений с поправкой на ЖД.

    Тип компоненты (``ComponentType``) различает только транспорт и
    проживание, поэтому вид транспорта берется у самих предложений: порог
    ослабляется, только когда вся выборка источника железнодорожная.
    """
    if offers and all(offer.offer_type == OfferType.RAIL.value for offer in offers):
        return eligibility.min_offers_after_dedup_rail
    return eligibility.min_offers_after_dedup


def check_eligibility(
    info: SourceCollectionInfo,
    counted_offers: Sequence[Offer],
    all_source_offers: Sequence[Offer],
    rules: ProfileRules,
    *,
    now: datetime | None = None,
) -> tuple[bool, list[str], float | None]:
    """Проверяет допуск источника к расчету компонента.

    Возвращает ``(допущен, причины_отказа, возраст_данных_в_минутах)``.
    """
    eligibility = rules.eligibility
    reasons: list[str] = []
    now = now or utcnow()
    age_minutes = minutes_between(info.collected_at, now)

    if eligibility.require_successful_call and not info.outcome.is_ok:
        reasons.append(f"Запрос завершился неуспешно: {info.outcome.value}")

    if age_minutes is not None and age_minutes > eligibility.max_data_age_minutes:
        reasons.append(
            f"Данные устарели: {age_minutes:.0f} мин > {eligibility.max_data_age_minutes} мин"
        )

    valid_offers = [offer for offer in all_source_offers if offer.is_valid]
    if len(valid_offers) < eligibility.min_valid_offers:
        reasons.append(
            f"Валидных предложений {len(valid_offers)} < {eligibility.min_valid_offers}"
        )

    min_after_dedup = _min_offers_after_dedup(eligibility, all_source_offers)
    if len(counted_offers) < min_after_dedup:
        reasons.append(
            f"После дедупликации и фильтрации осталось {len(counted_offers)} "
            f"< {min_after_dedup}"
        )

    total = len(all_source_offers)
    if total:
        invalid_ratio = sum(1 for offer in all_source_offers if not offer.is_valid) / total
        if invalid_ratio > eligibility.max_invalid_offer_ratio:
            reasons.append(
                f"Доля невалидных записей {invalid_ratio:.0%} > "
                f"{eligibility.max_invalid_offer_ratio:.0%}"
            )
        unclassified = [
            offer for offer in all_source_offers if offer.classification_status != "CLASSIFIED"
        ]
        if not eligibility.count_unreported_as_unclassified and info.unreported_attributes:
            # Источник не наказывается за признак, которого нет в его
            # контракте: остаются только те предложения, где не определено
            # что-то сверх заранее известных пробелов.
            unclassified = [
                offer
                for offer in unclassified
                if unclassified_attributes(offer) - info.unreported_attributes
            ]
        unclassified_ratio = len(unclassified) / total
        if unclassified_ratio > eligibility.max_unclassified_fare_ratio:
            reasons.append(
                f"Доля неклассифицированных предложений {unclassified_ratio:.0%} > "
                f"{eligibility.max_unclassified_fare_ratio:.0%}"
            )

    if (
        eligibility.min_source_confidence is not None
        and info.confidence_score is not None
        and info.confidence_score < eligibility.min_source_confidence
    ):
        reasons.append(
            f"Source Confidence {info.confidence_score:.0f} < "
            f"{eligibility.min_source_confidence:.0f}"
        )

    if info.is_synthetic and not rules.filters.allow_synthetic_sources:
        reasons.append("Синтетический источник не допущен профилем расчета")

    if rules.allowed_source_codes and info.source_code not in rules.allowed_source_codes:
        reasons.append("Источник не входит в список разрешенных профилем")
    if info.source_code in rules.excluded_source_codes:
        reasons.append("Источник исключен профилем")

    return (not reasons), reasons, age_minutes


# --------------------------------------------------------------------------- #
# Агрегация внутри источника
# --------------------------------------------------------------------------- #


def per_source_statistic(values: Sequence[Decimal], rules: ProfileRules) -> float | None:
    """Основной показатель источника. По умолчанию — медиана."""
    aggregation = rules.aggregation
    if not values:
        return None
    if aggregation.per_source_statistic == "MEDIAN":
        return median(values, aggregation.percentile_method)
    if aggregation.per_source_statistic == "P25":
        return percentile(values, 0.25, aggregation.percentile_method)
    if aggregation.per_source_statistic == "TRIMMED_MEAN":
        return trimmed_mean(values, aggregation.trim_ratio)
    return winsorized_mean(values, aggregation.trim_ratio)


def aggregate_source(
    info: SourceCollectionInfo,
    component: ComponentType,
    source_offers: Sequence[Offer],
    rules: ProfileRules,
    *,
    now: datetime | None = None,
) -> SourceAggregate:
    counted = [
        offer for offer in source_offers if offer.exclusion_reason == ExclusionReason.NONE.value
    ]
    prices = [to_decimal(offer.total_price) for offer in counted]
    prices = [price for price in prices if price is not None]

    eligible, reasons, age_minutes = check_eligibility(
        info, counted, source_offers, rules, now=now
    )
    distribution = describe(prices, rules.aggregation.percentile_method)

    return SourceAggregate(
        source_code=info.source_code,
        source_name=info.source_name,
        component=component,
        distribution=distribution,
        statistic=per_source_statistic(prices, rules),
        eligible=eligible and bool(prices),
        ineligibility_reasons=reasons or ([] if prices else ["Нет предложений для агрегации"]),
        offer_count=len(counted),
        valid_offer_count=sum(1 for offer in source_offers if offer.is_valid),
        invalid_offer_count=sum(1 for offer in source_offers if not offer.is_valid),
        unclassified_offer_count=sum(
            1 for offer in source_offers if offer.classification_status != "CLASSIFIED"
        ),
        duplicate_offer_count=sum(1 for offer in source_offers if offer.is_duplicate),
        outlier_offer_count=sum(1 for offer in source_offers if offer.is_outlier),
        excluded_offer_count=len(source_offers) - len(counted),
        data_age_minutes=age_minutes,
        latency_ms=info.latency_ms,
        is_synthetic=info.is_synthetic,
        confidence_score=info.confidence_score,
        outcome=info.outcome.value,
    )


# --------------------------------------------------------------------------- #
# Межисточниковая агрегация
# --------------------------------------------------------------------------- #


def combine_across_sources(values: Sequence[float], rules: ProfileRules) -> tuple[float | None, str]:
    """Объединяет статистики источников.

    * 1 источник — его значение (режим ``COMPLETE_SINGLE_SOURCE``);
    * 2 источника — среднее двух медиан (предписано методикой);
    * 3+ — медиана медиан.
    """
    aggregation = rules.aggregation
    clean = [value for value in values if value is not None]
    if not clean:
        return None, "NONE"
    if len(clean) == 1:
        return clean[0], "SINGLE_SOURCE"
    if len(clean) == 2:
        if aggregation.two_source_method == "MEAN":
            return mean(clean), "MEAN_OF_TWO"
        return median(clean, aggregation.percentile_method), "MEDIAN_OF_TWO"
    if aggregation.cross_source_method == "MEAN_OF_MEDIANS":
        return mean(clean), "MEAN_OF_MEDIANS"
    return median(clean, aggregation.percentile_method), "MEDIAN_OF_MEDIANS"


def aggregate_component(
    component: ComponentType,
    source_infos: dict[str, SourceCollectionInfo],
    offers_by_source: dict[str, list[Offer]],
    rules: ProfileRules,
    *,
    now: datetime | None = None,
) -> ComponentAggregate:
    """Полная агрегация компонента: по источникам, затем между источниками."""
    source_aggregates: list[SourceAggregate] = []
    for source_code in sorted(source_infos):
        info = source_infos[source_code]
        source_aggregates.append(
            aggregate_source(
                info, component, offers_by_source.get(source_code, []), rules, now=now
            )
        )

    eligible = [item for item in source_aggregates if item.eligible and item.statistic is not None]
    if not rules.aggregation.allow_single_source and len(eligible) == 1:
        eligible[0].eligible = False
        eligible[0].ineligibility_reasons.append(
            "Профиль запрещает расчет по единственному источнику"
        )
        eligible = []

    medians = [item.statistic for item in eligible if item.statistic is not None]
    combined_median, method = combine_across_sources(medians, rules)
    combined_p25, _ = combine_across_sources(
        [item.distribution.p25 for item in eligible if item.distribution.p25 is not None], rules
    )
    combined_p75, _ = combine_across_sources(
        [item.distribution.p75 for item in eligible if item.distribution.p75 is not None], rules
    )

    # P25 не может превышать медиану, а P75 — быть меньше: при разнонаправленных
    # источниках объединенные перцентили упорядочиваются принудительно.
    if combined_median is not None:
        if combined_p25 is not None:
            combined_p25 = min(combined_p25, combined_median)
        if combined_p75 is not None:
            combined_p75 = max(combined_p75, combined_median)

    # Размах объединяется не так, как перцентили: медиана минимумов ничего не
    # значила бы, а границей выборки является самое дешевое и самое дорогое
    # предложение среди всех допущенных источников.
    source_minimums = [
        item.distribution.min for item in eligible if item.distribution.min is not None
    ]
    source_maximums = [
        item.distribution.max for item in eligible if item.distribution.max is not None
    ]
    combined_min = min(source_minimums) if source_minimums else None
    combined_max = max(source_maximums) if source_maximums else None

    return ComponentAggregate(
        component=component,
        p25=_to_money(combined_p25),
        median=_to_money(combined_median),
        p75=_to_money(combined_p75),
        minimum=_to_money(combined_min),
        maximum=_to_money(combined_max),
        source_aggregates=source_aggregates,
        eligible_source_codes=[item.source_code for item in eligible],
        disagreement=relative_disagreement(medians),
        is_single_source=len(eligible) == 1,
        method=method,
        offer_count=sum(item.offer_count for item in eligible),
        excluded_offer_count=sum(item.excluded_offer_count for item in source_aggregates),
        outlier_offer_count=sum(item.outlier_offer_count for item in source_aggregates),
        unclassified_offer_count=sum(item.unclassified_offer_count for item in source_aggregates),
        contains_synthetic=any(item.is_synthetic for item in eligible),
    )


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #


def _to_money(value: float | None) -> Decimal | None:
    from tco.core.utils import money

    return money(value) if value is not None else None


def _decimal_out(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if isinstance(value, (int, float)) else None
