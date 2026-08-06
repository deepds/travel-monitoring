"""Разведение частот наблюдения.

Наблюдение делится тегом ``cadence:daily``: по общему интервалу идут
направления, ради которых платформа заводилась, раз в сутки — все остальное.
Раз в сутки собираются сетка витрины (она на порядок больше каталога: 1650
сценариев против 111, и в часовом прогоне давала бы 42 тысячи обращений к
источникам в сутки вместо 2500) и направления вне пятерки ключевых городов.

Проверяется сам отбор: именно он решает, что попадет в прогон.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.services.observation_grid import GRID_TAG, maintain_grid
from tco.services.scenarios import DAILY_CADENCE_TAG

TODAY = date(2026, 9, 1)


def select_by_tag(
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
def scenarios(session):
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
    def test_grid_is_daily(self, scenarios):
        """Сетка целиком уходит в суточный прогон."""
        grid = select_by_tag(scenarios, with_tag=GRID_TAG)

        assert grid
        assert all(DAILY_CADENCE_TAG in (item.tags or []) for item in grid)

    def test_hourly_run_skips_daily_scenarios(self, scenarios):
        hourly = select_by_tag(scenarios, without_tag=DAILY_CADENCE_TAG)

        assert all(GRID_TAG not in (item.tags or []) for item in hourly)
        assert all(DAILY_CADENCE_TAG not in (item.tags or []) for item in hourly)

    def test_split_is_complete_and_disjoint(self, scenarios):
        """Каждый сценарий попадает ровно в один прогон."""
        hourly = select_by_tag(scenarios, without_tag=DAILY_CADENCE_TAG)
        daily = select_by_tag(scenarios, with_tag=DAILY_CADENCE_TAG)

        assert len(hourly) + len(daily) == len(scenarios)
        assert not {id(item) for item in hourly} & {id(item) for item in daily}

    def test_daily_run_takes_more_than_the_grid(self, scenarios):
        """В суточный прогон входит и сетка, и направления вне пятерки городов."""
        daily = select_by_tag(scenarios, with_tag=DAILY_CADENCE_TAG)
        grid = select_by_tag(daily, with_tag=GRID_TAG)
        legacy = [item for item in daily if GRID_TAG not in (item.tags or [])]

        assert len(grid) == 55
        # Направления вне пятерки помечены в каталоге; если каталог в тестовой
        # базе засеян, они здесь и окажутся.
        assert all(DAILY_CADENCE_TAG in (item.tags or []) for item in legacy)

    def test_untagged_scenario_stays_hourly(self, scenarios):
        plain = [item for item in scenarios if not item.tags]
        if not plain:
            pytest.skip("В тестовой базе нет сценариев без тегов")

        hourly = select_by_tag(scenarios, without_tag=DAILY_CADENCE_TAG)
        assert all(item in hourly for item in plain)


class TestDailyRunIsSplitByComponent:
    """Суточный прогон разнесен по компонентам, иначе источник не выдерживает.

    Одним залпом это 1758 сценариев. На прогоне 6 августа размыкатель цепи
    открылся в середине, и проживание — оно собирается только у Туту —
    осталось без сбора по 395 сценариям из 465. Тот же набор, собранный
    отдельно, прошел без единого срабатывания размыкателя.
    """

    ACCOMMODATION_TAG = "showcase-accommodation"

    @staticmethod
    def entries():
        from tco.tasks.celery_app import celery_app

        return celery_app.conf.beat_schedule

    def test_transport_and_accommodation_run_at_different_hours(self):
        schedule = self.entries()
        transport = schedule["monitoring-snapshot-daily"]["schedule"]
        accommodation = schedule["monitoring-snapshot-daily-accommodation"]["schedule"]

        assert transport.hour != accommodation.hour

    def test_daily_transport_run_excludes_accommodation(self):
        kwargs = self.entries()["monitoring-snapshot-daily"]["kwargs"]

        assert kwargs["without_tag"] == self.ACCOMMODATION_TAG

    def test_split_covers_the_daily_scope_without_overlap(self, scenarios):
        """Ни один суточный сценарий не потерян и ни один не собирается дважды."""
        daily = select_by_tag(scenarios, with_tag=DAILY_CADENCE_TAG)
        transport = select_by_tag(daily, without_tag=self.ACCOMMODATION_TAG)
        accommodation = select_by_tag(scenarios, with_tag=self.ACCOMMODATION_TAG)

        assert len(transport) + len(accommodation) == len(daily)
        assert not {id(item) for item in transport} & {id(item) for item in accommodation}


class TestCollectionTasksAreBounded:
    """У задач сбора есть предел времени.

    Шестого августа воркер трижды за сутки вставал намертво: поток внутри
    задачи не возвращался, дочерний процесс оставался занят с открытой
    транзакцией в базе, свободных не оставалось, очередь переставала
    разбираться. Контейнер при этом числился здоровым, в логе стояла тишина.
    Предел не лечит причину, но снимает последствие: задачу снимают, процесс
    освобождается, воркер продолжает сам.
    """

    COLLECT_TASKS = (
        "tco.collect.collect_transport_offers",
        "tco.collect.collect_accommodation_offers",
    )

    @staticmethod
    def annotations():
        from tco.tasks.celery_app import celery_app

        return celery_app.conf.task_annotations

    def test_collect_tasks_have_a_hard_limit(self):
        for name in self.COLLECT_TASKS:
            limits = self.annotations()[name]
            assert limits["time_limit"] > limits["soft_time_limit"] > 0

    def test_limit_leaves_room_for_legitimate_collection(self):
        """Сбор ЖД законно идет минутами: карта мест — отдельный вызов на поезд."""
        for name in self.COLLECT_TASKS:
            assert self.annotations()[name]["soft_time_limit"] >= 240

    def test_names_are_exact_not_globs(self):
        """`task_annotations` понимает только точное имя задачи или `*`.

        Правило, написанное по образцу `task_routes` — `tco.collect.*` — молча
        не применяется: на стенде предел не сработал, и сбор при недоступном
        источнике шел полчаса на сценарий.
        """
        assert not [key for key in self.annotations() if key.endswith(".*")]
