"""Выбор наблюдений для агрегатов дашборда.

Дашборд показывает последний расчет каждого сценария. Отбор выполняет СУБД,
поэтому проверка идет через реальный запрос: ошибка здесь не падает, а тихо
завышает выборку, размывая медиану историей вместо текущего среза.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.services.dashboard import DashboardFilters, latest_runs

#: Наблюдения одного дня: свежее должно вытеснить остальные.
OBSERVATIONS = (
    (0, 100_000),
    (3, 200_000),
    (7, 300_000),
)


@pytest.fixture()
def scenario_with_history(session):
    """Сценарий с тремя наблюдениями разной давности."""
    cities = session.scalars(select(City).limit(2)).all()
    profile = session.scalars(select(CalculationProfile).limit(1)).first()
    assert len(cities) == 2 and profile is not None, "нужны справочники из bootstrap"

    departure = date.today() + timedelta(days=30)
    suffix = uuid.uuid4().hex[:8]
    scenario = TravelScenario(
        code=f"TEST-LATEST-{suffix}",
        name="Проверка выбора последнего наблюдения",
        origin_city_id=cities[0].id,
        destination_city_id=cities[1].id,
        departure_date=departure,
        return_date=departure + timedelta(days=5),
        nights=5,
        adults=2,
        transport_type="AVIA",
        accommodation_type="HOTEL",
        fingerprint=f"test-latest-{suffix}",
    )
    session.add(scenario)
    session.flush()

    now = utcnow()
    for days_ago, total in OBSERVATIONS:
        moment = now - timedelta(days=days_ago)
        session.add(
            ScenarioRun(
                scenario_id=scenario.id,
                run_type="MONITORING",
                status="SUCCESS",
                # ScenarioRun неизменяем и заполняет отметки явно — умолчания
                # ``TimestampMixin`` у него нет.
                created_at=moment,
                started_at=moment,
                completed_at=moment,
                observation_date=moment.date(),
                lead_time_days=30,
                profile_id=profile.id,
                profile_code=profile.code,
                profile_version=profile.version,
                normalization_version="1.0.0",
                engine_version="1.0.0",
                total_estimated_cost=total,
            )
        )
    session.flush()
    return scenario


def _runs_of(scenario, runs):
    """Расчеты только этого сценария: в базе теста живут и чужие наблюдения."""
    return [run for run in runs if run.scenario_id == scenario.id]


class TestLatestRuns:
    def test_only_the_freshest_run_is_returned(self, session, scenario_with_history):
        """Из трех наблюдений сценария в выборку попадает одно — свежее."""
        runs = _runs_of(scenario_with_history, latest_runs(session, DashboardFilters()))

        assert len(runs) == 1
        assert int(runs[0].total_estimated_cost) == OBSERVATIONS[0][1]

    def test_as_of_takes_the_freshest_run_before_the_date(
        self, session, scenario_with_history
    ):
        """Историческое сравнение берет последнее наблюдение на свою дату."""
        # Дата берется в UTC, как и у наблюдений: с локальной датой тест
        # разъезжается на переходе через полночь и падает раз в сутки.
        as_of = utcnow().date() - timedelta(days=1)
        runs = _runs_of(
            scenario_with_history, latest_runs(session, DashboardFilters(), as_of=as_of)
        )

        assert len(runs) == 1
        assert int(runs[0].total_estimated_cost) == OBSERVATIONS[1][1]

    def test_scenario_stays_available_for_grouping(self, session, scenario_with_history):
        """Направление берется из сценария, поэтому связь должна быть загружена."""
        runs = _runs_of(scenario_with_history, latest_runs(session, DashboardFilters()))

        assert runs[0].scenario.origin_city.code
        assert runs[0].scenario.destination_city.code
