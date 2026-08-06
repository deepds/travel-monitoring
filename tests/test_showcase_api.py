"""Контракт витрины вариантов отдыха.

Витрина читает наблюдения сетки и ничего не запрашивает у источников. Данных в
тестовой базе может не быть — тогда проверяется, что ответ остается корректным
и честно сообщает о пустоте: пустая строка без объяснения читается как
«поездка бесплатна».
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tco.services.observation_grid import CANONICAL_NIGHTS, SHOWCASE_CITIES

DEPARTURE = (date.today() + timedelta(days=14)).isoformat()
RETURN = (date.today() + timedelta(days=14 + CANONICAL_NIGHTS)).isoformat()


class TestCities:
    def test_lists_five_showcase_cities(self, client, viewer_headers):
        response = client.get("/api/v1/showcase/cities", headers=viewer_headers)

        assert response.status_code == 200
        body = response.json()
        codes = [item["code"] for item in body["items"]]
        assert codes == list(SHOWCASE_CITIES)
        assert body["horizon_days"] > 0

    def test_requires_authentication(self, client):
        assert client.get("/api/v1/showcase/cities").status_code in (401, 403)


class TestOptions:
    def test_returns_four_destinations(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/options",
            params={
                "origin": "MOW",
                "departure_date": DEPARTURE,
                "return_date": RETURN,
                "transport_type": "RAIL",
                "stars": "3",
            },
            headers=viewer_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["origin_code"] == "MOW"
        assert len(body["items"]) == len(SHOWCASE_CITIES) - 1
        assert "MOW" not in [item["destination_code"] for item in body["items"]]
        assert body["travelers"] == 1
        assert body["disclaimer"]

    def test_missing_components_are_named(self, client, viewer_headers):
        """Без наблюдений строка не выдается за бесплатную поездку."""
        body = client.get(
            "/api/v1/showcase/options",
            params={
                "origin": "MOW",
                "departure_date": DEPARTURE,
                "return_date": RETURN,
            },
            headers=viewer_headers,
        ).json()

        for item in body["items"]:
            if item["total"] is None:
                assert item["missing"], "не хватило компоненты — надо сказать какой"
            else:
                assert item["missing"] == []
                assert item["total"] == pytest.approx(
                    item["transport"] + item["accommodation"]
                )

    def test_rejects_city_outside_showcase(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/options",
            params={"origin": "KGD", "departure_date": DEPARTURE, "return_date": RETURN},
            headers=viewer_headers,
        )

        assert response.status_code == 422

    def test_rejects_return_before_departure(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/options",
            params={"origin": "MOW", "departure_date": RETURN, "return_date": DEPARTURE},
            headers=viewer_headers,
        )

        assert response.status_code == 422


class TestCurves:
    def test_transport_curve_covers_other_cities(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/transport-curve",
            params={"origin": "LED", "days": 30},
            headers=viewer_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["direction"] == "ONE_WAY"
        assert body["transport_type"] == "RAIL"
        codes = {item["destination_code"] for item in body["series"]}
        assert codes == set(SHOWCASE_CITIES) - {"LED"}

    def test_transport_curve_points_are_ordered(self, client, viewer_headers):
        body = client.get(
            "/api/v1/showcase/transport-curve",
            params={"origin": "MOW"},
            headers=viewer_headers,
        ).json()

        for series in body["series"]:
            dates = [point["departure_date"] for point in series["points"]]
            assert dates == sorted(dates)

    def test_accommodation_curve_covers_all_cities(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/accommodation-curve",
            params={"stars": "3"},
            headers=viewer_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert {item["city_code"] for item in body["series"]} == set(SHOWCASE_CITIES)
        assert body["nights"] == CANONICAL_NIGHTS
        assert body["stars"] == "3"


class TestTransportCurveFollowsTheSelectedMode:
    """Кривая проезда обязана меняться вместе с переключателем.

    Она была жестко привязана к ЖД: ни эндпоинт, ни фронтенд вида проезда не
    передавали, и переключение «Поезд / Самолет» меняло таблицу, но не график.
    """

    def test_rail_curve_is_one_way(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/transport-curve",
            params={"origin": "MOW", "transport_type": "RAIL"},
            headers=viewer_headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["transport_type"] == "RAIL"
        assert payload["direction"] == "ONE_WAY"

    def test_avia_curve_is_round_trip(self, client, viewer_headers):
        """У авиабилета цены плеча не существует: тариф круговой и неделимый."""
        response = client.get(
            "/api/v1/showcase/transport-curve",
            params={"origin": "MOW", "transport_type": "AVIA"},
            headers=viewer_headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["transport_type"] == "AVIA"
        assert payload["direction"] == "ROUND_TRIP"

    def test_mode_is_rail_by_default(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/transport-curve",
            params={"origin": "MOW"},
            headers=viewer_headers,
        )

        assert response.json()["transport_type"] == "RAIL"
