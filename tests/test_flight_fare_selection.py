"""Отбор авиапредложений: класс обслуживания и тарифные варианты одного рейса.

Оба правила выведены из сверки с сайтом источника: на живых данных бизнес-класс
составлял треть зачтенных предложений эконом-сценария, а каждый рейс попадал в
выборку по 4–6 раз — по разу на тарифный вариант. Медиана из-за этого измеряла
структуру тарифной сетки, а не цену поездки.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tco.core.enums import (
    AccommodationType,
    BaggageType,
    ClassificationStatus,
    ExclusionReason,
    MealType,
    OfferType,
    StarsFilter,
    TransportType,
    ValidityStatus,
)
from tco.db.models.offer import FlightOffer, Offer
from tco.engine.selection import ScenarioFilterSpec, run_selection
from tco.schemas.profile import ProfileRules

DEPARTURE = datetime(2026, 9, 18, 17, 10, tzinfo=timezone.utc)
RETURN = datetime(2026, 9, 23, 7, 10, tzinfo=timezone.utc)


def make_offer(
    price: int,
    *,
    flight_numbers: list[str],
    cabin: str = "ECONOMIC",
    baggage: BaggageType = BaggageType.CABIN_ONLY,
    fare_family: str = "Эконом Базовый",
    departure: datetime = DEPARTURE,
    source: str = "tutu_mcp",
) -> Offer:
    offer = Offer(
        source_code=source,
        source_offer_id=f"{'-'.join(flight_numbers)}:{fare_family}",
        offer_type=OfferType.FLIGHT.value,
        currency="RUB",
        total_price=price,
        technical_fingerprint=f"{flight_numbers}-{fare_family}-{price}",
        normalization_version="1.0.0",
        collected_at=DEPARTURE,
        # Умолчания колонок применяются при вставке, а эти объекты через базу
        # не проходят — статусы задаются явно.
        exclusion_reason=ExclusionReason.NONE.value,
        validity_status=ValidityStatus.VALID.value,
        classification_status=ClassificationStatus.CLASSIFIED.value,
        validation_messages=[],
        is_duplicate=False,
        is_outlier=False,
        matches_profile=True,
    )
    offer.flight = FlightOffer(
        flight_numbers=flight_numbers,
        marketing_carriers=["Аэрофлот"],
        outbound_departure_at=departure,
        inbound_departure_at=RETURN,
        cabin_class=cabin,
        fare_family=fare_family,
        baggage_type=baggage.value,
        refundability="NON_REFUNDABLE",
        outbound_stops=0,
        inbound_stops=0,
        passenger_count=2,
        price_basis="ALL_PASSENGERS",
    )
    return offer


@pytest.fixture()
def spec() -> ScenarioFilterSpec:
    return ScenarioFilterSpec(
        transport_type=TransportType.AVIA,
        flight_fare_type="CHEAPEST",
        rail_class=None,
        accommodation_type=AccommodationType.HOTEL,
        stars=StarsFilter.ANY,
        meal_type=MealType.ANY,
        cancellation_filter="ANY",
    )


@pytest.fixture()
def rules() -> ProfileRules:
    return ProfileRules.parse({})


def counted(offers):
    return [o for o in offers if o.exclusion_reason == ExclusionReason.NONE.value]


class TestCabinClass:
    def test_business_is_not_market_of_economy_scenario(self, spec, rules):
        """Бизнес-тариф не удовлетворяет ни одному тарифному режиму сценария."""
        offers = [
            make_offer(66_558, flight_numbers=["SU-1157", "SU-1264"]),
            make_offer(
                238_098,
                flight_numbers=["SU-1157", "SU-1264"],
                cabin="BUSINESS",
                baggage=BaggageType.CHECKED,
                fare_family="Бизнес Базовый",
            ),
        ]
        run_selection(offers, spec, rules)

        assert [int(o.total_price) for o in counted(offers)] == [66_558]
        assert offers[1].exclusion_detail == "CABIN_CLASS_MISMATCH"

    def test_unknown_cabin_is_kept(self, spec, rules):
        """Неизвестный класс не отбраковывается: часть источников его не сообщает."""
        offers = [make_offer(66_558, flight_numbers=["SU-1157"], cabin=None)]
        run_selection(offers, spec, rules)

        assert len(counted(offers)) == 1


class TestFareVariants:
    def test_one_flight_counts_once_at_its_cheapest_fare(self, spec, rules):
        """Из вариантов одного рейса остается самый дешевый."""
        offers = [
            make_offer(66_558, flight_numbers=["SU-1157", "SU-1264"], fare_family="Базовый"),
            make_offer(
                81_798,
                flight_numbers=["SU-1157", "SU-1264"],
                fare_family="Оптимум",
                baggage=BaggageType.CHECKED,
            ),
            make_offer(
                111_728,
                flight_numbers=["SU-1157", "SU-1264"],
                fare_family="Максимум",
                baggage=BaggageType.CHECKED,
            ),
        ]
        stats, _, _ = run_selection(offers, spec, rules)

        assert [int(o.total_price) for o in counted(offers)] == [66_558]
        assert stats.fare_variants == 2
        assert all(
            o.exclusion_reason == ExclusionReason.FARE_VARIANT.value
            for o in offers
            if int(o.total_price) != 66_558
        )

    def test_different_flights_are_kept_apart(self, spec, rules):
        """Схлопываются варианты одного рейса, а не разные рейсы."""
        offers = [
            make_offer(66_558, flight_numbers=["SU-1157", "SU-1264"]),
            make_offer(70_386, flight_numbers=["SU-1265", "SU-1256"]),
        ]
        run_selection(offers, spec, rules)

        assert len(counted(offers)) == 2

    def test_same_flight_at_two_sources_stays_comparable(self, spec, rules):
        """Один рейс у разных источников — межисточниковое сравнение, не вариант тарифа."""
        offers = [
            make_offer(66_558, flight_numbers=["SU-1157"], source="tutu_mcp"),
            make_offer(64_000, flight_numbers=["SU-1157"], source="other"),
        ]
        run_selection(offers, spec, rules)

        assert len(counted(offers)) == 2

    def test_median_drops_to_the_price_a_buyer_sees(self, spec, rules):
        """Три рейса по три тарифа: медиана считается по трем, а не по девяти."""
        offers = []
        for index, base in enumerate((66_558, 70_386, 74_000)):
            for markup, family in ((0, "Базовый"), (15_000, "Оптимум"), (45_000, "Максимум")):
                offers.append(
                    make_offer(
                        base + markup,
                        flight_numbers=[f"SU-{1000 + index}"],
                        fare_family=family,
                        baggage=BaggageType.CHECKED if markup else BaggageType.CABIN_ONLY,
                    )
                )
        run_selection(offers, spec, rules)

        assert sorted(int(o.total_price) for o in counted(offers)) == [66_558, 70_386, 74_000]
