"""Проезд в одну сторону.

Круговой тариф продается на конкретную пару дат: на произвольном интервале его
не существует вовсе, и витрина по таким датам показывала пустую клетку. Плечо
в одну сторону складывается на любой интервал — из него и собирается ответ на
вопрос «сколько будет стоить поездка с 12 по 19».

Одностороннее наблюдение выражается совпадением дат, а не отдельным полем.
Отдельное поле пришлось бы включить в отпечаток сценария — иначе поездка
«туда» и поездка «туда-обратно» на те же даты получили бы один отпечаток, — а
совпадение дат уже в нем есть и означает ровно это.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tco.core.enums import TransportType
from tco.connectors.contracts import TransportQuery

TODAY = date(2026, 9, 1)


def _query(*, departure: date, ret: date) -> TransportQuery:
    return TransportQuery(
        origin_city_code="MOW",
        destination_city_code="AER",
        origin_city_name="Москва",
        destination_city_name="Сочи",
        departure_date=departure,
        return_date=ret,
        adults=1,
        children_ages=(),
        transport_type=TransportType.RAIL,
    )


class TestQueryKnowsItsDirection:
    def test_equal_dates_mean_one_way(self):
        assert _query(departure=TODAY, ret=TODAY).is_one_way is True

    def test_different_dates_mean_round_trip(self):
        assert _query(departure=TODAY, ret=TODAY + timedelta(days=5)).is_one_way is False


class TestScenarioValidation:
    """Одностороннее наблюдение допустимо, но не вместе с проживанием."""

    @staticmethod
    def _scenario(**overrides):
        from tco.core.enums import (
            CancellationFilter,
            MealType,
            StarsFilter,
        )
        from tco.engine.validation import ScenarioInput

        params = {
            "origin_city_code": "MOW",
            "destination_city_code": "AER",
            "departure_date": date.today() + timedelta(days=10),
            "return_date": date.today() + timedelta(days=10),
            "adults": 1,
            "children_ages": (),
            "transport_type": TransportType.RAIL,
            "accommodation_type": None,
            "stars": StarsFilter.NOT_APPLICABLE,
            "meal_type": MealType.ANY,
            "cancellation_filter": CancellationFilter.ANY,
        }
        params.update(overrides)
        return ScenarioInput(**params)

    @staticmethod
    def _cities() -> dict:
        from tco.engine.validation import CityCapability

        return {
            "MOW": CityCapability(code="MOW", name="Москва", supports_avia=True, supports_rail=True),
            "AER": CityCapability(code="AER", name="Сочи", supports_avia=True, supports_rail=True),
        }

    def _validate(self, **overrides):
        from tco.engine.validation import validate_scenario

        return validate_scenario(self._scenario(**overrides), cities=self._cities())

    @staticmethod
    def _codes(result) -> set[str]:
        return {issue.code for issue in result.errors}

    def test_one_way_transport_is_valid(self):
        codes = self._codes(self._validate())

        assert "INVALID_DATE_ORDER" not in codes
        assert "ONE_WAY_WITH_ACCOMMODATION" not in codes

    def test_one_way_with_accommodation_is_rejected(self):
        """Брони на ноль ночей не существует — такой сценарий ничего не измерит."""
        from tco.core.enums import AccommodationType, StarsFilter

        codes = self._codes(
            self._validate(accommodation_type=AccommodationType.HOTEL, stars=StarsFilter.S3)
        )

        assert "ONE_WAY_WITH_ACCOMMODATION" in codes

    def test_return_before_departure_is_still_rejected(self):
        departure = date.today() + timedelta(days=10)

        codes = self._codes(
            self._validate(
                departure_date=departure, return_date=departure - timedelta(days=1)
            )
        )

        assert "INVALID_DATE_ORDER" in codes


class TestGridObservesBothKindsOfAirfare:
    """Авиа наблюдается двумя рядами, ЖД — плечом.

    Круговой тариф неделим: это одно число за поездку, и разложить его на плечи
    нельзя, не выдумывая. Но на произвольном интервале его нет. Плечи покрывают
    любой интервал, но в сумме дороже. Оба ряда правдивы и отвечают на разные
    вопросы, поэтому наблюдаются оба.
    """

    @staticmethod
    def drafts():
        from tco.services.observation_grid import grid_drafts

        return [d for d in grid_drafts(today=TODAY, horizon_days=1) if d.transport_type]

    def test_rail_is_observed_as_a_single_leg(self):
        rail = [d for d in self.drafts() if d.transport_type == TransportType.RAIL]

        assert len(rail) == 20
        for draft in rail:
            assert draft.return_date == draft.departure_date, "ЖД наблюдается плечом"

    def test_avia_is_observed_twice(self):
        avia = [d for d in self.drafts() if d.transport_type == TransportType.AVIA]

        assert len(avia) == 40, "20 маршрутов × два ряда наблюдений"
        one_way = [d for d in avia if d.return_date == d.departure_date]
        round_trip = [d for d in avia if d.return_date != d.departure_date]
        assert len(one_way) == 20
        assert len(round_trip) == 20

    def test_rows_are_separated_by_tag(self):
        """Ряды идут разными окнами: вместе это 1200 сценариев подряд."""
        from tco.services.observation_grid import AVIA_ONE_WAY_TAG, AVIA_TAG

        avia = [d for d in self.drafts() if d.transport_type == TransportType.AVIA]

        for draft in avia:
            expected = AVIA_ONE_WAY_TAG if draft.return_date == draft.departure_date else AVIA_TAG
            assert expected in draft.tags
            assert sum(tag in draft.tags for tag in (AVIA_TAG, AVIA_ONE_WAY_TAG)) == 1

    def test_one_way_scenarios_never_observe_accommodation(self):
        for draft in self.drafts():
            if draft.return_date == draft.departure_date:
                assert draft.accommodation_type is None


class TestNormalizationAcceptsASingleLeg:
    """У односторонней поездки обратного плеча нет по построению.

    Прежде нормализация помечала такое предложение ``INVALID_ROUTE`` —
    справедливо для кругового сценария и бессмысленно для одностороннего.
    """

    @staticmethod
    def _context(*, one_way: bool):
        import uuid

        from tco.core.enums import TransportType as TT
        from tco.normalization.normalizer import NormalizationContext

        departure = date.today() + timedelta(days=10)
        return NormalizationContext(
            scenario_id=uuid.uuid4(),
            market_snapshot_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            source_code="probe",
            connector_version="1.0.0",
            transport_type=TT.RAIL,
            accommodation_type=None,
            departure_date=departure,
            return_date=departure if one_way else departure + timedelta(days=5),
            adults=1,
            children_ages=(),
        )

    def test_context_knows_the_direction(self):
        assert self._context(one_way=True).is_one_way is True
        assert self._context(one_way=False).is_one_way is False

    @pytest.mark.parametrize("one_way", [True, False])
    def test_single_leg_validity_follows_the_scenario(self, one_way):
        from tco.connectors.contracts import ProviderRailOffer, ProviderSegment
        from tco.normalization.normalizer import normalize

        offer = ProviderRailOffer(
            source_offer_id="030Ч-COMPARTMENT-oneway",
            total_price=4000,
            price_per_place_outbound=4000,
            car_type_raw="КУПЕ",
            outbound_segments=[
                ProviderSegment(origin_code="2000000", destination_code="2064001")
            ],
            outbound_train_number="030Ч",
            is_round_trip=False,
        )

        result = normalize(offer, self._context(one_way=one_way))

        if one_way:
            assert result.offer.validity_status != "INVALID_ROUTE"
        else:
            assert result.offer.validity_status == "INVALID_ROUTE"
