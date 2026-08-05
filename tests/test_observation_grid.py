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
    CANONICAL_NIGHTS,
    GRID_TAG,
    SHOWCASE_ADULTS,
    SHOWCASE_CITIES,
    SHOWCASE_STARS,
    grid_drafts,
    maintain_grid,
    retire_expired,
)

TODAY = date(2026, 9, 1)

#: 20 направленных маршрутов на два вида транспорта плюс пять городов на три
#: категории звездности.
PER_DAY = len(SHOWCASE_CITIES) * (len(SHOWCASE_CITIES) - 1) * 2 + len(SHOWCASE_CITIES) * len(
    SHOWCASE_STARS
)


class TestGridComposition:
    def test_size_matches_horizon(self):
        drafts = grid_drafts(today=TODAY, horizon_days=30)

        assert PER_DAY == 55
        assert len(drafts) == 55 * 30

    def test_horizon_starts_tomorrow_and_has_no_gaps(self):
        drafts = grid_drafts(today=TODAY, horizon_days=7)
        days = sorted({draft.departure_date for draft in drafts})

        assert days[0] == TODAY + timedelta(days=1)
        assert days[-1] == TODAY + timedelta(days=7)
        assert len(days) == 7

    def test_transport_scenarios_observe_only_transport(self):
        drafts = [d for d in grid_drafts(today=TODAY, horizon_days=1) if d.transport_type]

        assert len(drafts) == 40
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

    def test_group_is_one_person_and_stay_is_canonical(self):
        for draft in grid_drafts(today=TODAY, horizon_days=1):
            assert draft.adults == SHOWCASE_ADULTS
            assert draft.return_date - draft.departure_date == timedelta(days=CANONICAL_NIGHTS)

    def test_every_scenario_is_tagged(self):
        assert all(GRID_TAG in draft.tags for draft in grid_drafts(today=TODAY, horizon_days=1))


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
