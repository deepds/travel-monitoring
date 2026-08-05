"""Разведение частот наблюдения между каталогом и сеткой витрины.

Сетка на порядок больше каталога: 1650 сценариев против 111. Если она попадет
в часовой прогон, обращений к источникам станет 42 тысячи в сутки вместо 2500 —
и упрутся они не в наш код, а в лимиты источников. Поэтому прогон умеет
отбирать сценарии по тегу, и проверяется здесь именно этот отбор.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.services.observation_grid import GRID_TAG, maintain_grid

TODAY = date(2026, 9, 1)


def _select_by_tag(
    scenarios: list[TravelScenario],
    *,
    with_tag: str | None = None,
    without_tag: str | None = None,
) -> list[TravelScenario]:
    """Тот же отбор, что делает плановый прогон."""
    result = scenarios
    if with_tag:
        result = [item for item in result if with_tag in (item.tags or [])]
    if without_tag:
        result = [item for item in result if without_tag not in (item.tags or [])]
    return result


@pytest.fixture()
def grid(session):
    profile = session.scalars(
        select(CalculationProfile).where(CalculationProfile.code == "mass-market")
    ).first()
    if profile is None:
        pytest.skip("Профиль mass-market не засеян в тестовой базе")
    maintain_grid(session, today=TODAY, horizon_days=1)
    session.flush()
    return session.scalars(
        select(TravelScenario).where(TravelScenario.deleted_at.is_(None))
    ).all()


class TestCadenceSplit:
    def test_catalogue_run_skips_the_grid(self, grid):
        selected = _select_by_tag(grid, without_tag=GRID_TAG)

        assert selected, "каталог направлений не должен опустеть"
        assert all(GRID_TAG not in (item.tags or []) for item in selected)

    def test_daily_run_takes_only_the_grid(self, grid):
        selected = _select_by_tag(grid, with_tag=GRID_TAG)

        assert len(selected) == 55
        assert all(GRID_TAG in (item.tags or []) for item in selected)

    def test_split_is_complete(self, grid):
        """Каждый сценарий попадает ровно в один из двух прогонов."""
        catalogue = _select_by_tag(grid, without_tag=GRID_TAG)
        showcase = _select_by_tag(grid, with_tag=GRID_TAG)

        assert len(catalogue) + len(showcase) == len(grid)
        assert not set(id(item) for item in catalogue) & set(id(item) for item in showcase)

    def test_untagged_scenario_stays_in_the_catalogue_run(self, session, grid):
        """Сценарий без тегов — это обычное направление каталога."""
        plain = [item for item in grid if not item.tags]
        if not plain:
            pytest.skip("В тестовой базе нет сценариев без тегов")

        assert all(item in _select_by_tag(grid, without_tag=GRID_TAG) for item in plain)
