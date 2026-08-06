"""Правила витрины: категории купе, невозвратные и прямые рейсы.

Постановка заказчика: купе только 2К и 2Д, авиа — невозвратные и прямые.
Правила выражены профилем, а не отдельным движком, поэтому проверяются там же,
где остальной отбор.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tco.core.enums import (
    AccommodationType,
    BaggageType,
    ClassificationStatus,
    ExclusionReason,
    MealType,
    OfferType,
    RailClass,
    StarsFilter,
    TransportType,
    ValidityStatus,
)
from tco.db.models.offer import FlightOffer, Offer, RailOffer
from tco.engine.selection import ScenarioFilterSpec, apply_profile_filter, SelectionStats
from tco.schemas.profile import ProfileRules

MOMENT = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

MASS_MARKET = {
    "filters": {
        "rail_service_classes": ["2К", "2Д"],
        "flights_non_refundable_only": True,
        "flights_direct_only": True,
    }
}


def _offer(offer_type: OfferType, price: int) -> Offer:
    return Offer(
        source_code="probe",
        source_offer_id=f"{offer_type.value}-{price}",
        offer_type=offer_type.value,
        currency="RUB",
        total_price=price,
        technical_fingerprint=f"{offer_type.value}-{price}",
        normalization_version="1.0.0",
        collected_at=MOMENT,
        exclusion_reason=ExclusionReason.NONE.value,
        validity_status=ValidityStatus.VALID.value,
        classification_status=ClassificationStatus.CLASSIFIED.value,
        validation_messages=[],
        is_duplicate=False,
        is_outlier=False,
        matches_profile=True,
    )


def flight(*, refundable: bool = False, stops: int = 0) -> Offer:
    offer = _offer(OfferType.FLIGHT, 20_000)
    offer.flight = FlightOffer(
        cabin_class="ECONOMIC",
        baggage_type=BaggageType.CABIN_ONLY.value,
        refundability="REFUNDABLE" if refundable else "NON_REFUNDABLE",
        outbound_stops=stops,
        inbound_stops=0,
        flight_numbers=["SU-1"],
        marketing_carriers=["Аэрофлот"],
        passenger_count=1,
        price_basis="ALL_PASSENGERS",
    )
    return offer


def rail(*, service_classes: list[str] | None) -> Offer:
    offer = _offer(OfferType.RAIL, 8_000)
    offer.rail = RailOffer(
        car_type=RailClass.COMPARTMENT.value,
        car_type_raw="КУПЕ",
        service_classes=service_classes if service_classes is not None else [],
        outbound_train_number="030Ч",
        passenger_count=1,
        is_round_trip=True,
        refundability="NON_REFUNDABLE",
    )
    return offer


def spec_for(transport: TransportType) -> ScenarioFilterSpec:
    return ScenarioFilterSpec(
        transport_type=transport,
        flight_fare_type="CHEAPEST",
        rail_class=RailClass.COMPARTMENT if transport == TransportType.RAIL else None,
        accommodation_type=AccommodationType.HOTEL,
        stars=StarsFilter.ANY,
        meal_type=MealType.ANY,
        cancellation_filter="ANY",
    )


def reason_for(offer: Offer, transport: TransportType, rules: ProfileRules) -> str | None:
    apply_profile_filter([offer], spec_for(transport), rules, SelectionStats(total=1))
    if offer.exclusion_reason == ExclusionReason.NONE.value:
        return None
    return offer.exclusion_detail


@pytest.fixture()
def mass_market() -> ProfileRules:
    return ProfileRules.parse(MASS_MARKET)


@pytest.fixture()
def baseline() -> ProfileRules:
    return ProfileRules.parse({})


class TestFlightRules:
    def test_refundable_fare_is_excluded(self, mass_market):
        assert reason_for(flight(refundable=True), TransportType.AVIA, mass_market) == "REFUNDABLE_FARE"

    def test_connection_is_excluded(self, mass_market):
        assert reason_for(flight(stops=1), TransportType.AVIA, mass_market) == "NOT_DIRECT"

    def test_direct_non_refundable_passes(self, mass_market):
        assert reason_for(flight(), TransportType.AVIA, mass_market) is None

    def test_baseline_keeps_both(self, baseline):
        """Базовая методика этих ограничений не знает — витрина их не навязывает."""
        assert reason_for(flight(refundable=True), TransportType.AVIA, baseline) is None
        assert reason_for(flight(stops=1), TransportType.AVIA, baseline) is None


class TestRailServiceClass:
    def test_requested_class_passes(self, mass_market):
        assert reason_for(rail(service_classes=["2К"]), TransportType.RAIL, mass_market) is None

    def test_other_class_is_excluded(self, mass_market):
        detail = reason_for(rail(service_classes=["2Ф"]), TransportType.RAIL, mass_market)
        assert detail == "RAIL_SERVICE_CLASS_MISMATCH"

    def test_unknown_class_is_excluded(self, mass_market):
        """Без сообщенного класса нельзя утверждать, что это 2К или 2Д."""
        detail = reason_for(rail(service_classes=[]), TransportType.RAIL, mass_market)
        assert detail == "RAIL_SERVICE_CLASS_UNKNOWN"

    def test_any_matching_class_is_enough(self, mass_market):
        """У вагона бывает несколько обозначений сразу."""
        assert reason_for(rail(service_classes=["2Ф", "2Д"]), TransportType.RAIL, mass_market) is None

    def test_baseline_ignores_service_class(self, baseline):
        assert reason_for(rail(service_classes=["2Ф"]), TransportType.RAIL, baseline) is None
        assert reason_for(rail(service_classes=[]), TransportType.RAIL, baseline) is None


class TestSelectionIsRepeatable:
    """Повторный отбор начинается с чистого листа.

    Пометка исключения, поставленная прошлым расчетом, не сбрасывалась, а в
    выборку берутся только предложения с `NONE`. Поэтому однажды отбракованное
    предложение не возвращалось никогда, и пересчет снимка новой методикой
    давал тот же результат, что и старой: правила применялись, но их вердикт
    не мог перебить запись прошлого прохода.
    """

    def test_offer_returns_when_the_rule_that_excluded_it_is_gone(self):
        from tco.engine.selection import run_selection

        offer = rail(service_classes=["2Ш"])
        spec = spec_for(TransportType.RAIL)
        strict = ProfileRules.parse({"filters": {"rail_service_classes": ["2К"]}})
        relaxed = ProfileRules.parse({"filters": {"rail_service_classes": []}})

        run_selection([offer], spec, strict)
        assert offer.exclusion_reason == ExclusionReason.PROFILE_FILTER.value

        run_selection([offer], spec, relaxed)
        assert offer.exclusion_reason == ExclusionReason.NONE.value

    def test_repeated_run_with_the_same_rules_is_stable(self):
        from tco.engine.selection import run_selection

        offer = rail(service_classes=["2Ш"])
        spec = spec_for(TransportType.RAIL)
        rules = ProfileRules.parse({"filters": {"rail_service_classes": []}})

        run_selection([offer], spec, rules)
        run_selection([offer], spec, rules)

        assert offer.exclusion_reason == ExclusionReason.NONE.value
