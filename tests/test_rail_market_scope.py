"""Что считается рыночным ЖД-предложением.

Правила выведены из сверки с сайтом РЖД по маршруту Калининград — Москва:
цены совпали до рубля, но в выборку попадали места целевого назначения, а
перевозчик целиком выпадал из расчета из-за порога, рассчитанного на авиа.
"""

from __future__ import annotations

from tco.connectors.rzd import _parse_trains
from tco.core.enums import OfferType
from tco.engine.aggregation import _min_offers_after_dedup
from tco.schemas.profile import ProfileRules


def car_group(*, name="КУПЕ", car_type="Compartment", price=6212.6, places=10, disabled=False):
    return {
        "CarTypeName": name,
        "CarType": car_type,
        "MinPrice": price,
        "TotalPlaceQuantity": places,
        "HasPlacesForDisabledPersons": disabled,
        "ServiceClasses": ["2Ф"],
    }


def payload(*groups):
    return {
        "Trains": [
            {
                "TrainNumber": "030Ч",
                "OriginStationCode": "2058000",
                "DestinationStationCode": "2000000",
                "CarGroups": list(groups),
            }
        ]
    }


class TestDisabledPlaces:
    def test_designated_places_are_not_market(self):
        """Купе для инвалидов дешевле обычного и рынком не является."""
        trains = _parse_trains(payload(car_group(price=6212.6), car_group(price=4168.9, places=2, disabled=True)))

        assert [float(train["price"]) for train in trains] == [6212.6]

    def test_regular_places_are_kept(self):
        trains = _parse_trains(payload(car_group(price=6212.6), car_group(price=5157.4, name="ПЛАЦ", car_type="ReservedSeat")))

        assert len(trains) == 2


class TestSoldOutCars:
    def test_car_without_places_is_not_an_observation(self):
        """Цена вагона без мест в продаже остается справочной."""
        trains = _parse_trains(payload(car_group(price=6212.6), car_group(price=3000.0, places=0)))

        assert [float(train["price"]) for train in trains] == [6212.6]

    def test_unknown_place_count_is_kept(self):
        """Отсутствие сведений о местах не повод отбрасывать предложение."""
        trains = _parse_trains(payload(car_group(places=None)))

        assert len(trains) == 1


class TestRailEligibilityThreshold:
    """Порог допуска источника ослаблен для ЖД: поездов на маршруте мало."""

    def setup_method(self):
        self.eligibility = ProfileRules.parse({}).eligibility

    def _offers(self, offer_type: OfferType, count: int):
        class Stub:
            def __init__(self, value: str) -> None:
                self.offer_type = value

        return [Stub(offer_type.value) for _ in range(count)]

    def test_rail_threshold_is_lower(self):
        assert _min_offers_after_dedup(self.eligibility, self._offers(OfferType.RAIL, 4)) == 2

    def test_flight_threshold_is_unchanged(self):
        assert _min_offers_after_dedup(self.eligibility, self._offers(OfferType.FLIGHT, 4)) == 3

    def test_mixed_sample_uses_the_strict_threshold(self):
        """Ослабление действует, только когда вся выборка железнодорожная."""
        mixed = self._offers(OfferType.RAIL, 2) + self._offers(OfferType.FLIGHT, 1)

        assert _min_offers_after_dedup(self.eligibility, mixed) == 3

    def test_empty_sample_uses_the_strict_threshold(self):
        assert _min_offers_after_dedup(self.eligibility, []) == 3
