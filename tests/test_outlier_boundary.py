"""Цена ровно на границе IQR выбросом не является.

Случай пришел с живых данных: плацкарт Калининград — Москва, где выборка
состоит из сочетаний двух поездов и границы IQR приходятся ровно на минимум и
максимум. Погрешность вычислений с плавающей точкой отбраковывала самый
дешевый вариант поездки, и медиана вырастала на четверть.
"""

from __future__ import annotations

from decimal import Decimal

from tco.engine.statistics import iqr_bounds
from tco.engine.selection import _outside_bounds

#: Плацкарт двух поездов: 3438.3 и 5157.4 за место, двое пассажиров, оба плеча.
SAMPLE = [Decimal("13753.2"), Decimal("17191.4"), Decimal("17191.4"), Decimal("20629.6")]


class TestBoundaryPrice:
    def test_cheapest_on_the_fence_is_kept(self):
        bounds = iqr_bounds(SAMPLE)

        assert bounds is not None
        # Граница вычисляется с погрешностью и оказывается чуть выше цены.
        assert bounds.lower > float(SAMPLE[0])
        assert _outside_bounds(float(SAMPLE[0]), bounds) is False

    def test_most_expensive_on_the_fence_is_kept(self):
        bounds = iqr_bounds(SAMPLE)

        assert _outside_bounds(float(SAMPLE[-1]), bounds) is False

    def test_real_outlier_is_still_flagged(self):
        """Допуск нужен на единицы последнего разряда, а не на рубли."""
        bounds = iqr_bounds(SAMPLE)

        assert _outside_bounds(float(bounds.lower) - 1, bounds) is True
        assert _outside_bounds(float(bounds.upper) + 1, bounds) is True

    def test_nothing_is_dropped_from_this_sample(self):
        bounds = iqr_bounds(SAMPLE)

        kept = [value for value in SAMPLE if not _outside_bounds(float(value), bounds)]
        assert kept == SAMPLE
