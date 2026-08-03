"""Scenario Confidence и Source Confidence (DELTA §7–§8).

Разграничение, которое нельзя размывать:

* ``Quality Score`` — техническое и статистическое качество расчета (0–100);
* ``Source Confidence`` — долгосрочное доверие к источнику как поставщику;
* ``Scenario Confidence`` — интерпретируемый уровень уверенности в том, что
  результат достаточно надежно представляет рыночный сценарий.

``INSUFFICIENT`` запрещает показывать итог как полноценную стоимость.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from tco.core.enums import ComponentType, ConfidenceLevel, SourceConfidenceLevel
from tco.engine.aggregation import ComponentAggregate
from tco.engine.quality import QualityScore
from tco.schemas.profile import ProfileRules
from tco.version import CONFIDENCE_FORMULA_VERSION

# --------------------------------------------------------------------------- #
# Scenario Confidence
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ScenarioConfidence:
    level: ConfidenceLevel
    reason: str
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)

    @property
    def allows_total_display(self) -> bool:
        """При ``INSUFFICIENT`` итог не показывается как полноценная стоимость."""
        return self.level != ConfidenceLevel.INSUFFICIENT

    def as_dict(self) -> dict:
        return {
            "level": self.level.value,
            "reason": self.reason,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
            "inputs": self.inputs,
            "allows_total_display": self.allows_total_display,
        }


def calculate_scenario_confidence(
    *,
    quality: QualityScore,
    transport: ComponentAggregate,
    accommodation: ComponentAggregate,
    rules: ProfileRules,
    source_confidence: dict[str, float] | None = None,
    challenge_verified: bool | None = None,
) -> ScenarioConfidence:
    """Определяет уровень уверенности в результате расчета."""
    thresholds = rules.confidence
    confidence_map = source_confidence or {}
    positive: list[str] = []
    negative: list[str] = []

    components = {
        ComponentType.TRANSPORT: transport,
        ComponentType.ACCOMMODATION: accommodation,
    }
    available = [item for item in components.values() if item.is_available]
    missing = [name for name, item in components.items() if not item.is_available]

    used_sources = sorted({code for item in available for code in item.eligible_source_codes})
    used_confidences = [confidence_map[code] for code in used_sources if code in confidence_map]
    min_source_confidence = min(used_confidences) if used_confidences else None

    min_sources_per_component = min((item.source_count for item in available), default=0)
    max_disagreement = max(
        (item.disagreement for item in available if item.disagreement is not None), default=None
    )
    total_offers = sum(item.offer_count for item in available)
    unclassified_ratio = _unclassified_ratio(available)

    inputs = {
        "quality_score": round(quality.score, 1),
        "available_components": [item.component.value for item in available],
        "missing_components": [name.value for name in missing],
        "min_sources_per_component": min_sources_per_component,
        "max_disagreement": round(max_disagreement, 4) if max_disagreement is not None else None,
        "total_offer_count": total_offers,
        "unclassified_ratio": round(unclassified_ratio, 4),
        "used_sources": used_sources,
        "min_source_confidence": min_source_confidence,
        "challenge_verified": challenge_verified,
        "formula_version": CONFIDENCE_FORMULA_VERSION,
    }

    # --- INSUFFICIENT: отсутствует обязательный компонент ----------------- #
    if missing:
        names = ", ".join(_component_title(name) for name in missing)
        return ScenarioConfidence(
            level=ConfidenceLevel.INSUFFICIENT,
            reason=f"Отсутствует обязательный компонент: {names}",
            negative_factors=[f"Компонент «{names}» не рассчитан"],
            inputs=inputs,
            positive_factors=[
                f"Компонент «{_component_title(item.component)}» рассчитан" for item in available
            ],
        )
    if quality.score < thresholds.low_min_quality:
        return ScenarioConfidence(
            level=ConfidenceLevel.INSUFFICIENT,
            reason=f"Quality Score {quality.score:.0f} ниже минимального порога "
            f"{thresholds.low_min_quality:.0f}",
            negative_factors=quality.reasons,
            inputs=inputs,
        )

    # --- Сбор факторов ---------------------------------------------------- #
    for component_type, item in components.items():
        title = _component_title(component_type)
        if item.is_single_source:
            negative.append(f"{title}: расчет по одному источнику ({item.eligible_source_codes[0]})")
        else:
            positive.append(f"{title}: {item.source_count} независимых источника")
        if item.disagreement is not None:
            if item.disagreement > rules.aggregation.high_disagreement_threshold:
                negative.append(f"{title}: высокое расхождение источников {item.disagreement:.0%}")
            else:
                positive.append(f"{title}: расхождение источников {item.disagreement:.0%}")
        if item.contains_synthetic:
            negative.append(f"{title}: в расчет включен синтетический источник (песочница)")

    if unclassified_ratio > thresholds.max_unclassified_ratio:
        negative.append(
            f"Доля неклассифицированных предложений {unclassified_ratio:.0%} превышает "
            f"порог {thresholds.max_unclassified_ratio:.0%}"
        )
    if min_source_confidence is not None:
        if min_source_confidence < thresholds.high_min_source_confidence:
            negative.append(
                f"Минимальный Source Confidence использованных источников "
                f"{min_source_confidence:.0f}"
            )
        else:
            positive.append(
                f"Source Confidence всех источников не ниже {min_source_confidence:.0f}"
            )
    if challenge_verified:
        positive.append("Похожий сценарий прошел проверку на challenge set")
    elif challenge_verified is False:
        negative.append("Похожий сценарий не подтвержден на challenge set")

    positive.append(f"Quality Score {quality.score:.0f}")
    negative.extend(quality.reasons)

    # --- Уровень ----------------------------------------------------------- #
    disagreement_ok = (
        max_disagreement is None or max_disagreement <= thresholds.high_max_disagreement
    )
    sources_ok = min_sources_per_component >= thresholds.high_min_sources_per_component
    confidence_ok = (
        min_source_confidence is None
        or min_source_confidence >= thresholds.high_min_source_confidence
    )
    unclassified_ok = unclassified_ratio <= thresholds.max_unclassified_ratio

    if (
        quality.score >= thresholds.high_min_quality
        and sources_ok
        and disagreement_ok
        and confidence_ok
        and unclassified_ok
    ):
        level = ConfidenceLevel.HIGH
        reason = (
            f"Quality Score {quality.score:.0f}, минимум "
            f"{min_sources_per_component} источника на компонент, низкое расхождение"
        )
    elif quality.score >= thresholds.medium_min_quality:
        level = ConfidenceLevel.MEDIUM
        reason = _medium_reason(quality, components, thresholds)
    else:
        level = ConfidenceLevel.LOW
        reason = (
            f"Quality Score {quality.score:.0f} в диапазоне "
            f"{thresholds.low_min_quality:.0f}–{thresholds.medium_min_quality:.0f}"
            + (
                f", расхождение источников {max_disagreement:.0%}"
                if max_disagreement is not None
                else ""
            )
        )

    return ScenarioConfidence(
        level=level,
        reason=reason,
        positive_factors=_dedupe(positive),
        negative_factors=_dedupe(negative),
        inputs=inputs,
    )


def _medium_reason(quality: QualityScore, components: dict, thresholds) -> str:  # noqa: ANN001
    single = [
        _component_title(name)
        for name, item in components.items()
        if item.is_available and item.is_single_source
    ]
    if single:
        return f"{', '.join(single)} рассчитан по одному источнику"
    if quality.score < thresholds.high_min_quality:
        return f"Quality Score {quality.score:.0f} ниже порога HIGH {thresholds.high_min_quality:.0f}"
    return "Не все условия высокой уверенности выполнены"


def _unclassified_ratio(components: list[ComponentAggregate]) -> float:
    total = sum(item.offer_count + item.excluded_offer_count for item in components)
    if not total:
        return 0.0
    return sum(item.unclassified_offer_count for item in components) / total


def _component_title(component: ComponentType) -> str:
    return "Транспорт" if component == ComponentType.TRANSPORT else "Проживание"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# --------------------------------------------------------------------------- #
# Source Confidence
# --------------------------------------------------------------------------- #

#: Веса факторов Source Confidence (DELTA §7.2).
SOURCE_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "technical_stability": 0.20,
    "field_completeness": 0.20,
    "cross_source_agreement": 0.20,
    "valid_offer_ratio": 0.15,
    "schema_stability": 0.10,
    "legal_reliability": 0.10,
    "manual_review": 0.05,
}

SOURCE_CONFIDENCE_TITLES: dict[str, str] = {
    "technical_stability": "Техническая стабильность за 30 дней",
    "field_completeness": "Полнота обязательных полей",
    "cross_source_agreement": "Согласованность с другими источниками",
    "valid_offer_ratio": "Доля валидных предложений",
    "schema_stability": "Стабильность схемы",
    "legal_reliability": "Юридическая и договорная надежность",
    "manual_review": "Результаты ручной проверки",
}


@dataclass(slots=True)
class SourceConfidenceResult:
    source_code: str
    score: float
    level: SourceConfidenceLevel
    factor_scores: dict[str, float]
    input_metrics: dict
    formula_version: str = CONFIDENCE_FORMULA_VERSION
    calculation_date: date | None = None

    def as_dict(self) -> dict:
        return {
            "source_code": self.source_code,
            "score": round(self.score, 1),
            "level": self.level.value,
            "factor_scores": {key: round(value, 4) for key, value in self.factor_scores.items()},
            "factor_weights": SOURCE_CONFIDENCE_WEIGHTS,
            "factor_titles": SOURCE_CONFIDENCE_TITLES,
            "input_metrics": self.input_metrics,
            "formula_version": self.formula_version,
            "calculation_date": self.calculation_date.isoformat() if self.calculation_date else None,
        }


def source_confidence_level(score: float) -> SourceConfidenceLevel:
    """Уровни доверия: HIGH 80–100, MEDIUM 60–79, LOW 40–59, UNTRUSTED 0–39."""
    if score >= 80:
        return SourceConfidenceLevel.HIGH
    if score >= 60:
        return SourceConfidenceLevel.MEDIUM
    if score >= 40:
        return SourceConfidenceLevel.LOW
    return SourceConfidenceLevel.UNTRUSTED


def calculate_source_confidence(
    *,
    source_code: str,
    success_rate: float | None,
    field_completeness: float | None,
    cross_source_agreement: float | None,
    valid_offer_ratio: float | None,
    schema_stability: float | None,
    legal_reliability: float | None,
    manual_review: float | None,
    input_metrics: dict | None = None,
    calculation_date: date | None = None,
) -> SourceConfidenceResult:
    """Считает Source Confidence из нормированных факторов 0..1.

    Отсутствующий фактор заменяется нейтральным значением 0.5, что явно
    фиксируется во входных метриках: «нет данных» не должно ни повышать,
    ни обнулять доверие.
    """
    raw = {
        "technical_stability": success_rate,
        "field_completeness": field_completeness,
        "cross_source_agreement": cross_source_agreement,
        "valid_offer_ratio": valid_offer_ratio,
        "schema_stability": schema_stability,
        "legal_reliability": legal_reliability,
        "manual_review": manual_review,
    }
    factor_scores = {
        key: (0.5 if value is None else max(0.0, min(1.0, float(value))))
        for key, value in raw.items()
    }
    score = sum(factor_scores[key] * SOURCE_CONFIDENCE_WEIGHTS[key] for key in factor_scores) * 100

    metrics = dict(input_metrics or {})
    metrics["missing_factors"] = sorted(key for key, value in raw.items() if value is None)

    return SourceConfidenceResult(
        source_code=source_code,
        score=round(score, 2),
        level=source_confidence_level(score),
        factor_scores=factor_scores,
        input_metrics=metrics,
        calculation_date=calculation_date,
    )
