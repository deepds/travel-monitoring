"""Разовый расчет витрины на даты вне сетки.

Сетка покрывает месяц вперед и поездку в пять ночей. На остальные даты витрина
предлагает посчитать разово — кнопкой «Рассчитать эти даты».

Дефект, ради которого написан файл: расчет проходил, задачи завершались
``SUCCESS`` с заполненным итогом, а таблица оставалась пустой. Витрина читала
только сценарии с тегом сетки, а разовый расчет заводит сценарий типа
``ON_DEMAND`` — тега у него нет и быть не должно, сетку ведет обслуживающая
задача. Пользователь видел спиннер и ту же пустую таблицу.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tco.core.enums import ScenarioType, StarsFilter, TransportType
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.services.observation_grid import CANONICAL_NIGHTS, SHOWCASE_ADULTS
from tco.services.showcase import options

#: Даты заведомо вне горизонта сетки: она покрывает месяц вперед.
DEPARTURE = date.today() + timedelta(days=200)
RETURN = DEPARTURE + timedelta(days=CANONICAL_NIGHTS)

TRANSPORT_MEDIAN = 7_000
HOTEL_MEDIAN = 22_000


def make_on_demand(
    session,
    *,
    origin: str = "MOW",
    destination: str = "AER",
    departure: date = DEPARTURE,
    ret: date = RETURN,
    adults: int = SHOWCASE_ADULTS,
    stars: str = StarsFilter.S3.value,
    transport: str = TransportType.RAIL.value,
) -> TravelScenario:
    """Сценарий разового расчета — совмещенный, как его заводит витрина."""
    cities = {
        city.code: city
        for city in session.scalars(select(City).where(City.code.in_([origin, destination]))).all()
    }
    profile = session.scalars(select(CalculationProfile).limit(1)).first()
    if len(cities) < 2 or profile is None:
        pytest.skip("нужны справочники городов из bootstrap")

    suffix = uuid.uuid4().hex[:8]
    scenario = TravelScenario(
        code=f"TEST-ONDEMAND-{suffix}",
        name="Разовый расчет витрины",
        scenario_type=ScenarioType.ON_DEMAND.value,
        origin_city_id=cities[origin].id,
        destination_city_id=cities[destination].id,
        departure_date=departure,
        return_date=ret,
        nights=(ret - departure).days,
        adults=adults,
        transport_type=transport,
        accommodation_type="HOTEL",
        stars=stars,
        fingerprint=f"test-ondemand-{suffix}",
    )
    session.add(scenario)
    session.flush()

    now = utcnow()
    session.add(
        ScenarioRun(
            scenario_id=scenario.id,
            run_type="ON_DEMAND",
            status="SUCCESS",
            created_at=now,
            started_at=now,
            completed_at=now,
            observation_date=now.date(),
            lead_time_days=(departure - now.date()).days,
            profile_id=profile.id,
            profile_code=profile.code,
            profile_version=profile.version,
            normalization_version="1.0.0",
            engine_version="1.0.0",
            transport_median=TRANSPORT_MEDIAN,
            hotel_median=HOTEL_MEDIAN,
            total_estimated_cost=TRANSPORT_MEDIAN + HOTEL_MEDIAN,
        )
    )
    session.flush()
    return scenario


def showcase(session, **overrides):
    payload = {
        "origin": "MOW",
        "departure_date": DEPARTURE,
        "return_date": RETURN,
        "transport_type": TransportType.RAIL,
        "stars": StarsFilter.S3,
    }
    payload.update(overrides)
    return options(session, **payload)


def row_of(payload, code: str) -> dict:
    return next(item for item in payload["items"] if item["destination_code"] == code)


class TestOnDemandFillsDatesOutsideTheGrid:
    def test_result_reaches_the_table(self, session):
        make_on_demand(session)

        row = row_of(showcase(session), "AER")

        # Цена идет вместе с тем, на чем она стоит: медиана, минимум, размер
        # выборки. Одна медиана без них выглядит увереннее, чем есть.
        assert row["transport"]["median"] == TRANSPORT_MEDIAN
        assert row["accommodation"]["median"] == HOTEL_MEDIAN
        assert row["total"]["median"] == TRANSPORT_MEDIAN + HOTEL_MEDIAN
        assert not row["missing"]

    def test_page_stops_offering_to_calculate(self, session):
        """`available` управляет пустым состоянием с кнопкой."""
        make_on_demand(session)

        assert showcase(session)["available"] is True

    def test_row_is_marked_as_calculated_once(self, session):
        """Разовый расчет — один снимок по запросу, а не наблюдение сетки."""
        make_on_demand(session)

        assert row_of(showcase(session), "AER")["on_demand"] is True

    def test_untouched_destinations_stay_empty(self, session):
        make_on_demand(session, destination="AER")

        row = row_of(showcase(session), "KZN")

        assert row["total"] is None
        assert row["on_demand"] is False


class TestOnDemandSelectionIsStrict:
    """Чужой расчет не должен попасть в ряд: на вид он неотличим."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("adults", SHOWCASE_ADULTS + 1),
            ("stars", StarsFilter.S5.value),
            ("transport", TransportType.AVIA.value),
            ("ret", RETURN + timedelta(days=2)),
        ],
    )
    def test_mismatched_parameters_are_ignored(self, session, field, value):
        make_on_demand(session, **{field: value})

        assert row_of(showcase(session), "AER")["total"] is None

    def test_monitoring_scenario_is_not_taken_as_on_demand(self, session):
        """Каталог наблюдает те же города, но своими параметрами."""
        scenario = make_on_demand(session)
        scenario.scenario_type = ScenarioType.MONITORING.value
        session.flush()

        assert row_of(showcase(session), "AER")["total"] is None
