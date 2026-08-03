"""Статистические примитивы методики (SCOPE-R P §7, §9)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tco.engine.statistics import (
    coefficient_of_variation,
    describe,
    iqr_bounds,
    mean,
    median,
    pct_change,
    percentile,
    relative_disagreement,
    trimmed_mean,
    winsorized_mean,
)


class TestPercentile:
    def test_linear_interpolation(self):
        assert percentile([1, 2, 3, 4], 0.5) == 2.5
        assert percentile([1, 2, 3, 4], 0.25) == 1.75
        assert percentile([1, 2, 3, 4], 0.75) == 3.25

    def test_nearest_rank(self):
        assert percentile([1, 2, 3, 4], 0.5, "NEAREST_RANK") == 2
        assert percentile([1, 2, 3, 4], 0.75, "NEAREST_RANK") == 3

    def test_single_value(self):
        assert percentile([10], 0.75) == 10.0

    def test_empty(self):
        assert percentile([], 0.5) is None

    def test_order_independent(self):
        assert percentile([4, 1, 3, 2], 0.5) == percentile([1, 2, 3, 4], 0.5)

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            percentile([1, 2], 1.5)

    def test_accepts_decimal(self):
        assert median([Decimal("10.00"), Decimal("20.00")]) == 15.0

    def test_p25_below_p75(self):
        values = [100, 250, 300, 480, 700, 1200]
        assert percentile(values, 0.25) < percentile(values, 0.5) < percentile(values, 0.75)


class TestIQR:
    def test_bounds(self):
        # Q1=2, Q3=4 при линейной интерполяции → IQR=2, границы −1 и 7.
        bounds = iqr_bounds([1, 2, 3, 4, 5], multiplier=1.5)
        assert bounds is not None
        assert bounds.q1 == 2.0
        assert bounds.q3 == 4.0
        assert bounds.iqr == 2.0
        assert bounds.lower == pytest.approx(-1.0)
        assert bounds.upper == pytest.approx(7.0)

    def test_detects_extreme_value(self):
        bounds = iqr_bounds([100, 110, 105, 108, 102, 5000])
        assert bounds is not None
        assert 5000 > bounds.upper

    def test_needs_two_values(self):
        assert iqr_bounds([42]) is None
        assert iqr_bounds([]) is None

    def test_multiplier_widens_bounds(self):
        narrow = iqr_bounds([1, 2, 3, 4, 5], multiplier=1.5)
        wide = iqr_bounds([1, 2, 3, 4, 5], multiplier=3.0)
        assert wide.upper > narrow.upper
        assert wide.lower < narrow.lower


class TestRobustMeans:
    def test_trimmed_mean_ignores_tails(self):
        values = [1, 100, 101, 102, 103, 1000]
        assert trimmed_mean(values, 0.2) < mean(values)

    def test_winsorized_mean_replaces_tails(self):
        values = [1, 100, 101, 102, 103, 1000]
        result = winsorized_mean(values, 0.2)
        assert result is not None
        # Хвосты заменяются, а не отбрасываются: результат остается между
        # усеченным средним и обычным.
        assert result < mean(values)

    def test_empty(self):
        assert trimmed_mean([]) is None
        assert winsorized_mean([]) is None
        assert mean([]) is None


class TestDisagreement:
    def test_relative_disagreement(self):
        # (120 − 80) / 100 = 0.4
        assert relative_disagreement([80, 100, 120]) == pytest.approx(0.4)

    def test_identical_sources_agree(self):
        assert relative_disagreement([100, 100, 100]) == 0.0

    def test_single_source_is_none(self):
        assert relative_disagreement([100]) is None

    def test_non_positive_median_is_none(self):
        assert relative_disagreement([0, 0]) is None

    def test_coefficient_of_variation(self):
        assert coefficient_of_variation([100, 100]) == 0.0
        assert coefficient_of_variation([50, 150]) > 0


class TestDescribe:
    def test_full_distribution(self):
        dist = describe([10, 20, 30, 40, 50])
        assert dist.count == 5
        assert dist.min == 10
        assert dist.max == 50
        assert dist.median == 30
        assert dist.p25 == 20
        assert dist.p75 == 40

    def test_empty_distribution(self):
        dist = describe([])
        assert dist.count == 0
        assert dist.median is None


class TestPctChange:
    def test_growth(self):
        assert pct_change(110, 100) == pytest.approx(0.1)

    def test_decline(self):
        assert pct_change(90, 100) == pytest.approx(-0.1)

    def test_zero_previous_is_none(self):
        assert pct_change(100, 0) is None

    def test_missing_values(self):
        assert pct_change(None, 100) is None
        assert pct_change(100, None) is None
