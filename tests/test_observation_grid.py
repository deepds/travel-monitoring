"""Скользящая сетка наблюдений витрины.

Сетка держит наблюдения на горизонт вперед и каждый день сдвигается. Проверяется
состав, идемпотентность повторного запуска и снятие прошедших дат — то есть
ровно те свойства, из-за нарушения которых кривая витрины разъехалась бы молча.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tco.core.enums import StarsFilter, TransportType
from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.services.observation_grid import (
    AVIA_ONE_WAY_TAG,
    AVIA_TAG,
    CANONICAL_NIGHTS,
    CONTROL_STAY_OFFSETS,
    CONTROL_STAY_TAG,
    GRID_TAG,
    RAIL_TAG,
    SHOWCASE_ADULTS,
    SHOWCASE_CITIES,
    SHOWCASE_STARS,
    STAY_NIGHTS,
    STAY_TAG,
    grid_drafts,
    maintain_grid,
    retire_expired,
    retire_out_of_grid,
)

TODAY = date(2026, 9, 1)

#: 20 направленных маршрутов: ЖД плечом, авиа двумя рядами (круговой и плечо),
#: плюс пять городов на три категории звездности.
ROUTES = len(SHOWCASE_CITIES) * (len(SHOWCASE_CITIES) - 1)
PER_DAY = ROUTES * 3 + len(SHOWCASE_CITIES) * len(SHOWCASE_STARS)

#: Контрольные пятидневные брони на три даты — они вне суточного состава.
CONTROL_TOTAL = len(SHOWCASE_CITIES) * len(SHOWCASE_STARS) * len(CONTROL_STAY_OFFSETS)


class TestGridComposition:
    def test_size_matches_horizon(self):
        drafts = grid_drafts(today=TODAY, horizon_days=30)

        assert PER_DAY == 75
        # 2250 суточных наблюдений плюс 45 контрольных пятидневных броней.
        assert len(drafts) == 75 * 30 + CONTROL_TOTAL == 2295

    def test_horizon_starts_tomorrow_and_has_no_gaps(self):
        drafts = grid_drafts(today=TODAY, horizon_days=7)
        days = sorted({draft.departure_date for draft in drafts})

        assert days[0] == TODAY + timedelta(days=1)
        assert days[-1] == TODAY + timedelta(days=7)
        assert len(days) == 7

    def test_transport_scenarios_observe_only_transport(self):
        drafts = [d for d in grid_drafts(today=TODAY, horizon_days=1) if d.transport_type]

        # 20 маршрутов: ЖД плечом, авиа круговым тарифом и плечом.
        assert len(drafts) == 60
        assert all(draft.accommodation_type is None for draft in drafts)
        assert {d.transport_type for d in drafts} == {TransportType.RAIL, TransportType.AVIA}

    def test_accommodation_scenarios_observe_only_accommodation(self):
        drafts = [d for d in grid_drafts(today=TODAY, horizon_days=1) if d.transport_type is None]

        assert len(drafts) == 15
        assert all(draft.accommodation_type is not None for draft in drafts)
        assert {d.stars for d in drafts} == set(SHOWCASE_STARS)

    def test_accommodation_origin_differs_from_destination(self):
        """Модель не допускает совпадения городов, а проживание от отправления не зависит."""
        drafts = [d for d in grid_drafts(today=TODAY, horizon_days=1) if d.transport_type is None]

        assert all(d.origin_city_code != d.destination_city_code for d in drafts)
        # Пара фиксирована, иначе отпечаток менялся бы и сетка пересоздавалась.
        moscow = [d for d in drafts if d.destination_city_code == "MOW"]
        assert {d.origin_city_code for d in moscow} == {"LED"}

    def test_group_is_one_person(self):
        for draft in grid_drafts(today=TODAY, horizon_days=1):
            assert draft.adults == SHOWCASE_ADULTS

    def test_round_trip_transport_keeps_the_canonical_stay(self):
        """Круговой тариф наблюдается на одну длительность: направления
        сравниваются между собой, а не поездки разной длины."""
        drafts = [
            d
            for d in grid_drafts(today=TODAY, horizon_days=1)
            if d.transport_type and d.return_date != d.departure_date
        ]

        assert drafts, "круговое наблюдение должно остаться"
        for draft in drafts:
            assert draft.return_date - draft.departure_date == timedelta(days=CANONICAL_NIGHTS)

    def test_every_scenario_is_tagged(self):
        assert all(GRID_TAG in draft.tags for draft in grid_drafts(today=TODAY, horizon_days=1))

    def test_schedule_tags_split_the_run(self):
        """Прогоны разнесены по времени: один залп источник не выдерживает."""
        drafts = grid_drafts(today=TODAY, horizon_days=1)
        tagged = {tag for draft in drafts for tag in draft.tags}

        assert {RAIL_TAG, AVIA_TAG, STAY_TAG} <= tagged
        # Каждый сценарий попадает ровно в одно окно, иначе он либо соберется
        # дважды, либо не соберется вовсе.
        windows = (RAIL_TAG, AVIA_TAG, AVIA_ONE_WAY_TAG, STAY_TAG, CONTROL_STAY_TAG)
        for draft in drafts:
            assert sum(tag in draft.tags for tag in windows) == 1


class TestStayIsObservedForOneNight:
    """Основной ряд проживания — бронь на одну ночь.

    Цена ночи внутри пятидневной брони усреднена по будням и выходным сразу:
    заезд в среду означает, что в брони среда, четверг, пятница, суббота и
    воскресенье. На графике по датам заезда это размазывает недельную волну по
    пяти соседним точкам, и вопрос «когда в городе дорого» остается без ответа.
    """

    def test_main_series_books_a_single_night(self):
        drafts = [
            d
            for d in grid_drafts(today=TODAY, horizon_days=30)
            if d.transport_type is None and STAY_TAG in d.tags
        ]

        assert len(drafts) == len(SHOWCASE_CITIES) * len(SHOWCASE_STARS) * 30
        for draft in drafts:
            assert draft.return_date - draft.departure_date == timedelta(days=STAY_NIGHTS)

    def test_control_series_is_three_dates_out_of_thirty(self):
        """Пятидневные остались, но контрольными: 45 сценариев вместо 450."""
        drafts = [
            d
            for d in grid_drafts(today=TODAY, horizon_days=30)
            if CONTROL_STAY_TAG in d.tags
        ]

        assert len(drafts) == len(SHOWCASE_CITIES) * len(SHOWCASE_STARS) * 3
        for draft in drafts:
            assert draft.return_date - draft.departure_date == timedelta(days=CANONICAL_NIGHTS)

    def test_control_dates_fall_on_different_weekdays(self):
        """Расхождение оценки само зависит от дня недели."""
        weekdays = {(TODAY + timedelta(days=offset)).weekday() for offset in CONTROL_STAY_OFFSETS}

        assert len(weekdays) == len(CONTROL_STAY_OFFSETS)


@pytest.fixture()
def mass_market_profile(session):
    """Профиль витрины: без него сетка намеренно не строится."""
    profile = session.scalars(
        select(CalculationProfile).where(CalculationProfile.code == "mass-market")
    ).first()
    if profile is None:
        pytest.skip("Профиль mass-market не засеян в тестовой базе")
    return profile


class TestMaintenance:
    def test_refuses_without_showcase_profile(self, session, monkeypatch):
        """Без своего профиля сетка считалась бы по чужой методике."""
        monkeypatch.setattr("tco.services.observation_grid.GRID_PROFILE_CODE", "no-such-profile")
        report = maintain_grid(session, today=TODAY, horizon_days=1)

        assert report.profile_missing is True
        assert report.created == 0

    def test_creates_then_reuses(self, session, mass_market_profile):
        first = maintain_grid(session, today=TODAY, horizon_days=1)
        assert first.created == PER_DAY
        assert first.existing == 0

        second = maintain_grid(session, today=TODAY, horizon_days=1)
        assert second.created == 0
        assert second.existing == PER_DAY

    def test_retires_past_departures(self, session, mass_market_profile):
        maintain_grid(session, today=TODAY, horizon_days=1)
        session.flush()

        # На следующий день после единственной даты горизонта она уже в прошлом.
        retired = retire_expired(session, today=TODAY + timedelta(days=2))
        assert retired == PER_DAY

        session.flush()
        alive = [
            item
            for item in session.scalars(
                select(TravelScenario).where(TravelScenario.deleted_at.is_(None))
            ).all()
            if GRID_TAG in (item.tags or [])
        ]
        assert alive == []

    def test_drops_scenarios_that_left_the_composition(self, session, mass_market_profile):
        """Изменение состава сетки обязано снимать выбывшее с наблюдения.

        Иначе прежние сценарии продолжают собираться молча: они активны,
        помечены сеткой и попадают в суточный прогон — а бюджет обращений к
        источнику при этом удваивается. Так осталось бы 450 пятидневных броней
        проживания после перехода на однодневные.
        """
        maintain_grid(session, today=TODAY, horizon_days=1)
        session.flush()
        alive = session.scalars(
            select(TravelScenario)
            .where(TravelScenario.deleted_at.is_(None))
            .where(TravelScenario.is_showcase_grid.is_(True))
        ).all()
        assert len(alive) == PER_DAY

        # Состав из одного сценария: остальные выбыли.
        keep = {alive[0].fingerprint}
        dropped = retire_out_of_grid(session, keep, today=TODAY, horizon_days=1)

        assert dropped == PER_DAY - 1

    def test_empty_composition_never_wipes_the_grid(self, session, mass_market_profile):
        """Пустой состав означает ошибку вызова, а не выбывшие сценарии."""
        maintain_grid(session, today=TODAY, horizon_days=1)
        session.flush()

        assert retire_out_of_grid(session, set(), today=TODAY, horizon_days=1) == 0

    def test_existing_scenarios_get_missing_schedule_tags(self, session, mass_market_profile):
        """Сценарий без нового тега не попал бы ни в одно окно прогона."""
        maintain_grid(session, today=TODAY, horizon_days=1)
        session.flush()
        scenario = session.scalars(
            select(TravelScenario)
            .where(TravelScenario.is_showcase_grid.is_(True))
            .where(TravelScenario.transport_type == TransportType.RAIL.value)
        ).first()
        assert scenario is not None
        scenario.tags = [tag for tag in scenario.tags if tag != RAIL_TAG]
        session.flush()

        report = maintain_grid(session, today=TODAY, horizon_days=1)

        assert report.retagged >= 1
        assert RAIL_TAG in scenario.tags

    def test_retirement_spares_other_scenarios(self, session, mass_market_profile):
        """Снимаются только сценарии сетки, каталог наблюдения не трогается."""
        before = session.scalars(
            select(TravelScenario)
            .where(TravelScenario.deleted_at.is_(None))
            .where(TravelScenario.departure_date < TODAY)
        ).all()
        untagged = [item for item in before if GRID_TAG not in (item.tags or [])]

        retire_expired(session, today=TODAY)

        still_alive = session.scalars(
            select(TravelScenario).where(TravelScenario.deleted_at.is_(None))
        ).all()
        assert all(item in still_alive for item in untagged)
