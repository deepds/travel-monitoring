"""Quality Score — техническое и статистическое качество конкретного расчета.

Стартовая модель весов задана методикой (SCOPE-R P §12) и целиком вынесена
в профиль. Балл всегда сопровождается разложением по факторам и списком
причин снижения — без этого показатель невозможно ни объяснить, ни
откалибровать на challenge set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from tco.core.enums import ComponentType
from tco.engine.aggregation import ComponentAggregate
from tco.schemas.profile import ProfileRules

#: Человекочитаемые названия факторов для UI и объяснений.
FACTOR_TITLES: dict[str, str] = {
    "component_completeness": "Полнота компонентов",
    "source_count": "Число источников",
    "offer_count": "Число предложений",
    "source_agreement": "Согласованность источников",
    "freshness": "Свежесть данных",
    "connector_stability": "Стабильность коннекторов",
}


@dataclass(slots=True)
class QualityScore:
    score: float
    factor_scores: dict[str, float]
    weights: dict[str, float]
    contributions: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    partial_success_threshold: float = 60.0

    @property
    def meets_threshold(self) -> bool:
        return self.score >= self.partial_success_threshold

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "factor_scores": {key: round(value, 4) for key, value in self.factor_scores.items()},
            "weights": {key: round(value, 4) for key, value in self.weights.items()},
            "contributions": {
                key: round(value, 2) for key, value in self.contributions.items()
            },
            "factor_titles": FACTOR_TITLES,
            "reasons": self.reasons,
            "partial_success_threshold": self.partial_success_threshold,
        }


def calculate_quality_score(
    *,
    transport: ComponentAggregate,
    accommodation: ComponentAggregate,
    rules: ProfileRules,
    connector_stability: dict[str, float] | None = None,
    observed: Sequence[ComponentType] = (ComponentType.TRANSPORT, ComponentType.ACCOMMODATION),
) -> QualityScore:
    """Считает Quality Score в диапазоне 0–100.

    ``observed`` — компоненты, которые сценарий обязался наблюдать. Полнота
    меряется по ним: иначе сценарий «только транспорт» терял бы 30 % оценки за
    отсутствие проживания, которого он и не запрашивал.
    """
    quality = rules.quality
    weights = quality.weights.normalized()
    stability_map = connector_stability or {}
    reasons: list[str] = []

    all_components = {
        ComponentType.TRANSPORT: transport,
        ComponentType.ACCOMMODATION: accommodation,
    }
    components = {key: all_components[key] for key in observed} or all_components
    available = [item for item in components.values() if item.is_available]

    # --- Полнота компонентов -------------------------------------------- #
    completeness = len(available) / len(components)
    if not transport.is_available:
        reasons.append("Отсутствует транспортный компонент")
    if not accommodation.is_available:
        reasons.append("Отсутствует компонент проживания")

    # --- Число источников ------------------------------------------------ #
    if available:
        source_scores = [
            min(item.source_count, quality.target_source_count) / quality.target_source_count
            for item in available
        ]
        source_count_score = sum(source_scores) / len(source_scores)
        for component_type, item in components.items():
            if item.is_available and item.source_count < quality.target_source_count:
                reasons.append(
                    f"{_component_title(component_type)}: источников {item.source_count} "
                    f"из целевых {quality.target_source_count}"
                )
    else:
        source_count_score = 0.0

    # --- Число предложений ----------------------------------------------- #
    if available:
        offer_scores = [
            min(item.offer_count / quality.target_offer_count, 1.0) for item in available
        ]
        offer_count_score = sum(offer_scores) / len(offer_scores)
        for component_type, item in components.items():
            if item.is_available and item.offer_count < quality.target_offer_count:
                reasons.append(
                    f"{_component_title(component_type)}: предложений {item.offer_count} "
                    f"из целевых {quality.target_offer_count}"
                )
    else:
        offer_count_score = 0.0

    # --- Согласованность источников -------------------------------------- #
    threshold = rules.aggregation.high_disagreement_threshold
    agreement_values: list[float] = []
    for component_type, item in components.items():
        if not item.is_available:
            continue
        if item.source_count < 2 or item.disagreement is None:
            agreement_values.append(quality.single_source_agreement_score)
            reasons.append(
                f"{_component_title(component_type)}: расчет по одному источнику — "
                "межисточниковое расхождение не определено"
            )
            continue
        ratio = min(item.disagreement / threshold, 1.0) if threshold > 0 else 0.0
        agreement_values.append(1.0 - ratio)
        if item.disagreement > threshold:
            reasons.append(
                f"{_component_title(component_type)}: расхождение источников "
                f"{item.disagreement:.0%} превышает порог {threshold:.0%}"
            )
    agreement_score = sum(agreement_values) / len(agreement_values) if agreement_values else 0.0

    # --- Свежесть -------------------------------------------------------- #
    max_age = rules.eligibility.max_data_age_minutes
    ages = [
        source.data_age_minutes
        for item in available
        for source in item.source_aggregates
        if source.eligible and source.data_age_minutes is not None
    ]
    if ages and max_age > 0:
        worst_age = max(ages)
        freshness_score = max(0.0, 1.0 - min(worst_age / max_age, 1.0))
        if worst_age > max_age * 0.5:
            reasons.append(f"Возраст данных {worst_age:.0f} мин при пороге {max_age} мин")
    else:
        freshness_score = 1.0 if available else 0.0

    # --- Стабильность коннекторов ---------------------------------------- #
    used_sources = sorted(
        {code for item in available for code in item.eligible_source_codes}
    )
    if used_sources:
        stability_values = [
            stability_map.get(code, quality.default_connector_stability) for code in used_sources
        ]
        stability_score = sum(stability_values) / len(stability_values)
        weak = [
            code
            for code in used_sources
            if stability_map.get(code, quality.default_connector_stability) < 0.9
        ]
        if weak:
            reasons.append(f"Историческая стабильность источников ниже 90%: {', '.join(weak)}")
    else:
        stability_score = 0.0

    factor_scores = {
        "component_completeness": _clamp(completeness),
        "source_count": _clamp(source_count_score),
        "offer_count": _clamp(offer_count_score),
        "source_agreement": _clamp(agreement_score),
        "freshness": _clamp(freshness_score),
        "connector_stability": _clamp(stability_score),
    }
    contributions = {key: factor_scores[key] * weights[key] * 100.0 for key in factor_scores}
    score = sum(contributions.values())

    return QualityScore(
        score=round(score, 2),
        factor_scores=factor_scores,
        weights=weights,
        contributions=contributions,
        reasons=reasons,
        partial_success_threshold=quality.partial_success_threshold,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _component_title(component: ComponentType) -> str:
    return "Транспорт" if component == ComponentType.TRANSPORT else "Проживание"
