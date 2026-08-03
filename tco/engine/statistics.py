"""Статистические примитивы движка.

Реализованы вручную (без numpy) по двум причинам: воспроизводимость результата
не должна зависеть от версии сторонней библиотеки, а метод расчета перцентиля
обязан быть явно описан в методике.

Метод ``LINEAR`` эквивалентен ``numpy.percentile(..., method="linear")``
и ``pandas.Series.quantile(interpolation="linear")``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence

from tco.core.utils import to_decimal

PercentileMethod = Literal["LINEAR", "NEAREST_RANK"]


def _clean(values: Sequence[float | Decimal]) -> list[float]:
    """Приводит выборку к отсортированному списку конечных float."""
    cleaned: list[float] = []
    for value in values:
        dec = to_decimal(value)
        if dec is None:
            continue
        cleaned.append(float(dec))
    cleaned.sort()
    return cleaned


def percentile(
    values: Sequence[float | Decimal],
    q: float,
    method: PercentileMethod = "LINEAR",
) -> float | None:
    """Перцентиль уровня ``q`` ∈ [0, 1].

    >>> percentile([1, 2, 3, 4], 0.5)
    2.5
    >>> percentile([1, 2, 3, 4], 0.25)
    1.75
    >>> percentile([10], 0.75)
    10.0
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError("q должно быть в диапазоне [0, 1]")
    ordered = _clean(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]

    if method == "NEAREST_RANK":
        import math

        rank = max(1, math.ceil(q * len(ordered)))
        return ordered[rank - 1]

    position = q * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index
    return ordered[lower_index] * (1.0 - weight) + ordered[upper_index] * weight


def median(values: Sequence[float | Decimal], method: PercentileMethod = "LINEAR") -> float | None:
    return percentile(values, 0.5, method)


def trimmed_mean(values: Sequence[float | Decimal], trim_ratio: float = 0.1) -> float | None:
    """Среднее после отбрасывания ``trim_ratio`` с каждого края."""
    ordered = _clean(values)
    if not ordered:
        return None
    if not 0.0 <= trim_ratio < 0.5:
        raise ValueError("trim_ratio должно быть в диапазоне [0, 0.5)")
    cut = int(len(ordered) * trim_ratio)
    core = ordered[cut : len(ordered) - cut] if len(ordered) - 2 * cut > 0 else ordered
    return sum(core) / len(core)


def winsorized_mean(values: Sequence[float | Decimal], trim_ratio: float = 0.1) -> float | None:
    """Среднее с заменой хвостов граничными значениями."""
    ordered = _clean(values)
    if not ordered:
        return None
    if not 0.0 <= trim_ratio < 0.5:
        raise ValueError("trim_ratio должно быть в диапазоне [0, 0.5)")
    cut = int(len(ordered) * trim_ratio)
    if cut == 0 or len(ordered) - 2 * cut <= 0:
        return sum(ordered) / len(ordered)
    low, high = ordered[cut], ordered[len(ordered) - cut - 1]
    adjusted = [min(max(value, low), high) for value in ordered]
    return sum(adjusted) / len(adjusted)


def mean(values: Sequence[float | Decimal]) -> float | None:
    ordered = _clean(values)
    if not ordered:
        return None
    return sum(ordered) / len(ordered)


@dataclass(frozen=True, slots=True)
class Distribution:
    """Описательные статистики выборки (SCOPE-R P §9 «внутри источника»)."""

    count: int
    min: float | None
    p25: float | None
    median: float | None
    p75: float | None
    max: float | None
    mean: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "min": self.min,
            "p25": self.p25,
            "median": self.median,
            "p75": self.p75,
            "max": self.max,
            "mean": self.mean,
        }

    @property
    def iqr(self) -> float | None:
        if self.p25 is None or self.p75 is None:
            return None
        return self.p75 - self.p25


def describe(values: Sequence[float | Decimal], method: PercentileMethod = "LINEAR") -> Distribution:
    ordered = _clean(values)
    if not ordered:
        return Distribution(count=0, min=None, p25=None, median=None, p75=None, max=None, mean=None)
    return Distribution(
        count=len(ordered),
        min=ordered[0],
        p25=percentile(ordered, 0.25, method),
        median=percentile(ordered, 0.5, method),
        p75=percentile(ordered, 0.75, method),
        max=ordered[-1],
        mean=sum(ordered) / len(ordered),
    )


@dataclass(frozen=True, slots=True)
class IQRBounds:
    lower: float
    upper: float
    q1: float
    q3: float
    iqr: float


def iqr_bounds(
    values: Sequence[float | Decimal],
    multiplier: float = 1.5,
    method: PercentileMethod = "LINEAR",
) -> IQRBounds | None:
    """Границы выбросов: ``Q1 − k·IQR`` и ``Q3 + k·IQR`` (SCOPE-R P §7)."""
    ordered = _clean(values)
    if len(ordered) < 2:
        return None
    q1 = percentile(ordered, 0.25, method)
    q3 = percentile(ordered, 0.75, method)
    if q1 is None or q3 is None:
        return None
    spread = q3 - q1
    return IQRBounds(
        lower=q1 - multiplier * spread,
        upper=q3 + multiplier * spread,
        q1=q1,
        q3=q3,
        iqr=spread,
    )


def relative_disagreement(values: Sequence[float | Decimal]) -> float | None:
    """Межисточниковое расхождение: ``(max − min) / median`` по медианам источников.

    Возвращает ``None``, если источников меньше двух или медиана не положительна.
    """
    ordered = _clean(values)
    if len(ordered) < 2:
        return None
    center = median(ordered)
    if not center or center <= 0:
        return None
    return (ordered[-1] - ordered[0]) / center


def coefficient_of_variation(values: Sequence[float | Decimal]) -> float | None:
    ordered = _clean(values)
    if len(ordered) < 2:
        return None
    avg = sum(ordered) / len(ordered)
    if avg == 0:
        return None
    variance = sum((value - avg) ** 2 for value in ordered) / (len(ordered) - 1)
    return (variance**0.5) / abs(avg)


def pct_change(current: float | Decimal | None, previous: float | Decimal | None) -> float | None:
    """Относительное изменение в долях. ``None``, если база отсутствует или нулевая."""
    cur, prev = to_decimal(current), to_decimal(previous)
    if cur is None or prev is None or prev == 0:
        return None
    return float((cur - prev) / prev)
