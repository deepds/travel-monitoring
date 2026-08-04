"""Состав сценария: наблюдение за одной компонентой.

Сценарий не обязан следить за поездкой целиком. Ключевое различие, которое
проверяется здесь: ненаблюдаемая компонента — **не** недостающая. Сценарий
«только перелет» дает полноценный результат, а не вечный `PARTIAL_SUCCESS`,
и не теряет 30 % оценки качества за проживание, которого не запрашивал.
"""

from __future__ import annotations

import pytest

from decimal import Decimal

from tco.core.enums import ComponentType, TransportType
from tco.engine.aggregation import ComponentAggregate
from tco.engine.confidence import calculate_scenario_confidence
from tco.engine.pipeline import _combine_components, _determine_status
from tco.engine.quality import calculate_quality_score
from tco.schemas.profile import ProfileRules

TRANSPORT_ONLY = (ComponentType.TRANSPORT,)
STAY_ONLY = (ComponentType.ACCOMMODATION,)
BOTH = (ComponentType.TRANSPORT, ComponentType.ACCOMMODATION)


def _aggregate(
    median: float | None,
    *,
    component: ComponentType = ComponentType.TRANSPORT,
    sources: int = 2,
) -> ComponentAggregate:
    """Компонент с заданной медианой; ``None`` — компонент недоступен."""
    value = Decimal(str(median)) if median is not None else None
    codes = [f"src{index}" for index in range(sources)] if median is not None else []
    return ComponentAggregate(
        component=component,
        p25=value,
        median=value,
        p75=value,
        source_aggregates=[],
        eligible_source_codes=codes,
        disagreement=0.05 if median is not None else None,
        is_single_source=len(codes) == 1,
        method="MEDIAN_OF_MEDIANS" if codes else "NONE",
        offer_count=20 if median is not None else 0,
        excluded_offer_count=0,
        outlier_offer_count=0,
        unclassified_offer_count=0,
    )


@pytest.fixture()
def rules() -> ProfileRules:
    return ProfileRules.parse({})


class TestTotalCost:
    """Итог складывается только из наблюдаемых компонент."""

    def test_transport_only_gives_full_total(self, rules):
        totals = _combine_components(
            _aggregate(30000), _aggregate(None), traveler_count=2, rules=rules,
            observed=TRANSPORT_ONLY,
        )

        assert totals["total_estimated_cost"] is not None
        assert float(totals["total_estimated_cost"]) == 30000
        assert float(totals["price_per_person"]) == 15000

    def test_stay_only_gives_full_total(self, rules):
        totals = _combine_components(
            _aggregate(None), _aggregate(48000), traveler_count=2, rules=rules,
            observed=STAY_ONLY,
        )

        assert float(totals["total_estimated_cost"]) == 48000

    def test_both_components_are_summed(self, rules):
        totals = _combine_components(
            _aggregate(30000), _aggregate(48000), traveler_count=2, rules=rules, observed=BOTH
        )

        assert float(totals["total_estimated_cost"]) == 78000

    def test_missing_observed_component_cancels_total(self, rules):
        """Наблюдаемая, но не собранная компонента по-прежнему отменяет итог."""
        totals = _combine_components(
            _aggregate(30000), _aggregate(None), traveler_count=2, rules=rules, observed=BOTH
        )

        assert totals["total_estimated_cost"] is None

    def test_transport_share_is_absent_without_transport(self, rules):
        totals = _combine_components(
            _aggregate(None), _aggregate(48000), traveler_count=2, rules=rules,
            observed=STAY_ONLY,
        )

        assert totals["transport_share"] is None


class TestStatus:
    """Полнота меряется по наблюдаемым компонентам."""

    def test_transport_only_is_success(self, rules):
        status, components = _determine_status(
            _aggregate(30000), _aggregate(None), {}, [], observed=TRANSPORT_ONLY
        )

        assert status.value == "SUCCESS"
        assert "PARTIAL_HOTEL_MISSING" not in [item.value for item in components]

    def test_stay_only_is_success(self, rules):
        status, components = _determine_status(
            _aggregate(None), _aggregate(48000), {}, [], observed=STAY_ONLY
        )

        assert status.value == "SUCCESS"
        assert "PARTIAL_TRANSPORT_MISSING" not in [item.value for item in components]

    def test_full_scenario_without_stay_is_partial(self, rules):
        status, components = _determine_status(
            _aggregate(30000), _aggregate(None), {}, [], observed=BOTH
        )

        assert status.value == "PARTIAL_SUCCESS"
        assert "PARTIAL_HOTEL_MISSING" in [item.value for item in components]

    def test_transport_only_without_data_is_not_success(self, rules):
        status, _ = _determine_status(
            _aggregate(None), _aggregate(48000), {}, [], observed=TRANSPORT_ONLY
        )

        assert status.value != "SUCCESS"


class TestQuality:
    """Оценка качества не наказывает за незапрошенную компоненту."""

    def test_transport_only_gets_full_completeness(self, rules):
        quality = calculate_quality_score(
            transport=_aggregate(30000),
            accommodation=_aggregate(None),
            rules=rules,
            observed=TRANSPORT_ONLY,
        )

        assert quality.factor_scores["component_completeness"] == 1.0

    def test_same_data_as_full_scenario_scores_lower(self, rules):
        single = calculate_quality_score(
            transport=_aggregate(30000),
            accommodation=_aggregate(None),
            rules=rules,
            observed=TRANSPORT_ONLY,
        )
        full = calculate_quality_score(
            transport=_aggregate(30000),
            accommodation=_aggregate(None),
            rules=rules,
            observed=BOTH,
        )

        assert single.score > full.score, "полнота у односоставного сценария не должна страдать"


class TestConfidence:
    """Уровень уверенности тоже считается по наблюдаемым компонентам."""

    def test_missing_unobserved_component_is_not_a_gap(self, rules):
        quality = calculate_quality_score(
            transport=_aggregate(30000),
            accommodation=_aggregate(None),
            rules=rules,
            observed=TRANSPORT_ONLY,
        )

        confidence = calculate_scenario_confidence(
            quality=quality,
            transport=_aggregate(30000),
            accommodation=_aggregate(None),
            rules=rules,
            observed=TRANSPORT_ONLY,
        )

        assert confidence.level.value != "INSUFFICIENT"


class TestIdentity:
    """Состав входит в отпечаток: это разные сценарии."""

    def test_scope_changes_fingerprint(self):
        from datetime import date

        from tco.core.enums import (
            AccommodationType,
            CancellationFilter,
            FlightFareType,
            MealType,
            StarsFilter,
        )
        from tco.engine.fingerprint import ScenarioKey, scenario_fingerprint

        def key(*, stay: AccommodationType | None) -> ScenarioKey:
            return ScenarioKey(
                origin_city_code="MOW",
                destination_city_code="AER",
                departure_date=date(2026, 9, 18),
                return_date=date(2026, 9, 23),
                adults=2,
                children_ages=(),
                transport_type=TransportType.AVIA,
                flight_fare_type=FlightFareType.CHEAPEST,
                rail_class=None,
                accommodation_type=stay,
                stars=StarsFilter.ANY,
                meal_type=MealType.ANY,
                cancellation_filter=CancellationFilter.ANY,
            )

        full = scenario_fingerprint(key(stay=AccommodationType.HOTEL))
        transport_only = scenario_fingerprint(key(stay=None))

        assert full != transport_only

    def test_unobserved_parameters_do_not_affect_identity(self):
        """Звездность в неиспользуемом поле не создает нового сценария."""
        from datetime import date

        from tco.core.enums import (
            CancellationFilter,
            FlightFareType,
            MealType,
            StarsFilter,
        )
        from tco.engine.fingerprint import ScenarioKey, scenario_fingerprint

        def key(*, stars: StarsFilter) -> ScenarioKey:
            return ScenarioKey(
                origin_city_code="MOW",
                destination_city_code="AER",
                departure_date=date(2026, 9, 18),
                return_date=date(2026, 9, 23),
                adults=2,
                children_ages=(),
                transport_type=TransportType.AVIA,
                flight_fare_type=FlightFareType.CHEAPEST,
                rail_class=None,
                accommodation_type=None,
                stars=stars,
                meal_type=MealType.ANY,
                cancellation_filter=CancellationFilter.ANY,
            )

        assert scenario_fingerprint(key(stars=StarsFilter.ANY)) == scenario_fingerprint(
            key(stars=StarsFilter.S4)
        )


class TestApi:
    """Создание односоставного сценария через API."""

    @staticmethod
    def _payload(base: dict, **overrides) -> dict:
        payload = dict(base, scenario_type="MONITORING")
        payload.update(overrides)
        return payload

    def test_transport_only_scenario_is_created(self, client, admin_headers, unique_scenario_payload):
        payload = self._payload(unique_scenario_payload, accommodation_type=None, stars="ANY")

        response = client.post("/api/v1/scenarios", json=payload, headers=admin_headers)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["accommodation_type"] is None
        assert body["transport_type"] == "AVIA"
        assert "NONE" in body["code"], "состав должен быть виден в коде сценария"

    def test_stay_only_scenario_is_created(self, client, admin_headers, unique_scenario_payload):
        payload = self._payload(
            unique_scenario_payload, transport_type=None, flight_fare_type=None
        )

        response = client.post("/api/v1/scenarios", json=payload, headers=admin_headers)

        assert response.status_code == 201, response.text
        assert response.json()["transport_type"] is None

    def test_empty_scope_is_rejected(self, client, admin_headers, unique_scenario_payload):
        payload = self._payload(
            unique_scenario_payload,
            transport_type=None,
            flight_fare_type=None,
            accommodation_type=None,
        )

        response = client.post("/api/v1/scenarios", json=payload, headers=admin_headers)

        assert response.status_code == 422, "сценарий без компонент ничего не измеряет"

    def test_single_component_scenario_calculates(
        self, client, admin_headers, unique_scenario_payload, sandbox_profile
    ):
        payload = self._payload(unique_scenario_payload, accommodation_type=None, stars="ANY")
        created = client.post("/api/v1/scenarios", json=payload, headers=admin_headers).json()

        response = client.post(
            "/api/v1/calculations", json={"scenario_id": created["id"]}, headers=admin_headers
        )

        assert response.status_code in (200, 202), response.text
        body = response.json()
        run = body.get("run") or client.get(
            f"/api/v1/calculations/{body['job_id']}", headers=admin_headers
        ).json().get("run")
        assert run is not None, "расчет должен состояться по одной компоненте"
        assert "PARTIAL_HOTEL_MISSING" not in run["component_statuses"]
