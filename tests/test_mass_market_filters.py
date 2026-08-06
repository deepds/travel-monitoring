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
from tco.db.models.offer import AccommodationOffer, FlightOffer, Offer, RailOffer
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


def hotel(*, room_name: str | None) -> Offer:
    offer = _offer(OfferType.ACCOMMODATION, 7_000)
    offer.accommodation = AccommodationOffer(
        accommodation_type=AccommodationType.HOTEL.value,
        stars=3,
        stars_status="RATED",
        meal_type=MealType.NO_MEALS.value,
        cancellation_type="NON_REFUNDABLE",
        capacity_confirmed=True,
        room_name=room_name,
        nights=1,
        room_count=1,
    )
    return offer


def stay_reason(offer: Offer, rules: ProfileRules) -> str | None:
    spec = ScenarioFilterSpec(
        transport_type=None,
        flight_fare_type=None,
        rail_class=None,
        accommodation_type=AccommodationType.HOTEL,
        stars=StarsFilter.S3,
        meal_type=MealType.ANY,
        cancellation_filter="ANY",
    )
    apply_profile_filter([offer], spec, rules, SelectionStats(total=1))
    if offer.exclusion_reason == ExclusionReason.NONE.value:
        return None
    return offer.exclusion_detail


class TestApartmentsAreNotMassMarket:
    """Апартаменты и хостелы исключены по прямому указанию руководителя.

    Это другой продукт с другой ценой: в казанской выдаче апартаменты идут по
    12–14 тысяч рублей за ночь при медиане отелей около семи, и их доля там
    9,4 % против 0,1 % в московской. Смешанная выборка описывала бы не
    гостиничный рынок города, а его смесь с арендой квартир — причем в разных
    городах в разной пропорции, то есть города переставали бы быть
    сопоставимыми.

    Проверка идет по категории номера, потому что тип объекта источник в
    ответе не возвращает вовсе.
    """

    RULES = ProfileRules.parse(
        {"filters": {"accommodation_excluded_room_keywords": ["апартамент", "хостел"]}}
    )

    def test_apartment_is_excluded(self):
        detail = stay_reason(hotel(room_name="Апартаменты с кухней"), self.RULES)
        assert detail == "ROOM_CATEGORY_EXCLUDED"

    def test_case_does_not_matter(self):
        assert stay_reason(hotel(room_name="АПАРТАМЕНТЫ"), self.RULES) == "ROOM_CATEGORY_EXCLUDED"

    def test_ordinary_room_passes(self):
        assert stay_reason(hotel(room_name="Стандартный двухместный"), self.RULES) is None

    def test_missing_room_name_is_not_a_reason_to_drop(self):
        """Иначе терялись бы обычные отели там, где выдача беднее."""
        assert stay_reason(hotel(room_name=None), self.RULES) is None

    def test_baseline_keeps_apartments(self):
        """Базовая методика наблюдает рынок целиком — витрина ей не указ."""
        assert stay_reason(hotel(room_name="Апартаменты"), ProfileRules.parse({})) is None


class TestUnclassifiedIsAboutTheQuestionAsked:
    """Пробел в признаке считается пробелом, только если признак спрашивают.

    «Предложение не классифицировано» должно означать «нельзя сказать,
    подходит ли оно под требования». Если требования нет, вопрос не
    возникает. Прежде получалось внутреннее противоречие: питание не
    запрашивалось, фильтр предложение пропускал — и оно же считалось
    неклассифицированным, за что наказывался источник.

    На живых данных это проявилось после перехода на полный обход выдачи:
    условия отмены Туту заполняет не у всех объектов, и на 200 предложениях
    доля неизвестных дошла до 78 % при пороге 40 %. Источник переставал
    допускаться, и проживание выходило NO_DATA при двух сотнях собранных
    предложений.
    """

    @staticmethod
    def _spec(*, meal: MealType, cancellation: str) -> ScenarioFilterSpec:
        return ScenarioFilterSpec(
            transport_type=None,
            flight_fare_type=None,
            rail_class=None,
            accommodation_type=AccommodationType.HOTEL,
            stars=StarsFilter.S3,
            meal_type=meal,
            cancellation_filter=cancellation,
        )

    @staticmethod
    def _unknown_offer() -> Offer:
        offer = _offer(OfferType.ACCOMMODATION, 7_000)
        offer.accommodation = AccommodationOffer(
            accommodation_type=AccommodationType.HOTEL.value,
            stars=3,
            stars_status="RATED",
            meal_type=MealType.UNKNOWN.value,
            cancellation_type="UNKNOWN",
            capacity_confirmed=True,
            room_name="Стандартный",
            nights=1,
            room_count=1,
        )
        return offer

    def test_unasked_attributes_are_not_gaps(self):
        from tco.engine.selection import unclassified_attributes

        spec = self._spec(meal=MealType.ANY, cancellation="ANY")

        assert unclassified_attributes(self._unknown_offer(), spec) == frozenset()

    def test_asked_meal_is_a_gap_when_unknown(self):
        from tco.engine.selection import unclassified_attributes

        spec = self._spec(meal=MealType.BREAKFAST, cancellation="ANY")

        assert "MEAL" in unclassified_attributes(self._unknown_offer(), spec)

    def test_asked_cancellation_is_a_gap_when_unknown(self):
        from tco.engine.selection import unclassified_attributes

        spec = self._spec(meal=MealType.ANY, cancellation="FREE_CANCELLATION")

        assert "CANCELLATION" in unclassified_attributes(self._unknown_offer(), spec)

    def test_without_spec_every_gap_counts(self):
        """Вызов без сценария остается прежним: он не знает, что спрашивали."""
        from tco.engine.selection import unclassified_attributes

        found = unclassified_attributes(self._unknown_offer())

        assert {"MEAL", "CANCELLATION"} <= found

    def test_capacity_is_always_checked(self):
        """Состав туристов задан у любого сценария, поэтому вопрос есть всегда."""
        from tco.engine.selection import unclassified_attributes

        offer = self._unknown_offer()
        offer.accommodation.capacity_confirmed = False
        spec = self._spec(meal=MealType.ANY, cancellation="ANY")

        assert unclassified_attributes(offer, spec) == frozenset({"CAPACITY"})


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
