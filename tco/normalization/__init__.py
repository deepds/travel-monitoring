"""Нормализация предложений источников в единую модель данных."""

from tco.normalization.classify import (
    baggage_satisfies,
    cancellation_satisfies,
    classify_accommodation_type,
    classify_baggage,
    classify_cancellation,
    classify_meal,
    classify_rail_class,
    classify_refundability,
    meal_satisfies,
    stars_satisfies,
)
from tco.normalization.normalizer import (
    NormalizationContext,
    NormalizedOffer,
    normalize,
    normalize_many,
    rail_passenger_multiplier,
)

__all__ = [
    "NormalizationContext",
    "NormalizedOffer",
    "baggage_satisfies",
    "cancellation_satisfies",
    "classify_accommodation_type",
    "classify_baggage",
    "classify_cancellation",
    "classify_meal",
    "classify_rail_class",
    "classify_refundability",
    "meal_satisfies",
    "normalize",
    "normalize_many",
    "rail_passenger_multiplier",
    "stars_satisfies",
]
