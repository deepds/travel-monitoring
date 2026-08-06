"""Контракт витрины вариантов отдыха.

Витрина читает наблюдения сетки и ничего не запрашивает у источников. Данных в
тестовой базе может не быть — тогда проверяется, что ответ остается корректным
и честно сообщает о пустоте: пустая строка без объяснения читается как
«поездка бесплатна».
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tco.services.observation_grid import CANONICAL_NIGHTS, SHOWCASE_CITIES, STAY_NIGHTS

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
                assert item["total"]["median"] == pytest.approx(
                    item["transport"]["median"] + item["accommodation"]["median"]
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
        assert body["nights"] == STAY_NIGHTS
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


class TestAccommodationCurveShowsEveryCity:
    """График проживания отвечает на вопрос «когда в городе дорого».

    Он не про выбор направления, а про сезон и день недели, поэтому город
    отправления из него не исключается: свой город — такой же ответ, как
    чужой. Прежнее поведение (исключать выбранный город) делало график
    зависимым от выбора в блоке выше, хотя вопрос там задается другой.
    """

    def test_origin_city_is_present_too(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/accommodation-curve",
            params={"origin": "LED", "stars": "3"},
            headers=viewer_headers,
        )

        assert response.status_code == 200
        codes = {item["city_code"] for item in response.json()["series"]}
        assert codes == set(SHOWCASE_CITIES)

    def test_without_origin_all_cities_are_returned(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/accommodation-curve",
            params={"stars": "3"},
            headers=viewer_headers,
        )

        codes = {item["city_code"] for item in response.json()["series"]}
        assert len(codes) == 5

    def test_city_outside_showcase_is_rejected(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/accommodation-curve",
            params={"origin": "AAQ", "stars": "3"},
            headers=viewer_headers,
        )

        assert response.status_code in (400, 404, 422)


class TestObservationDates:
    """Список дат наблюдения — условие того, чтобы пустой график был объясним."""

    def test_returns_dates_with_data(self, client, viewer_headers):
        response = client.get(
            "/api/v1/showcase/observation-dates", headers=viewer_headers
        )

        assert response.status_code == 200
        body = response.json()
        assert "items" in body and "latest" in body
        for item in body["items"]:
            assert item["runs"] > 0, "дата без наблюдений в списке бессмысленна"

    def test_curve_accepts_an_observation_date(self, client, viewer_headers):
        """Вчерашний срез доступен: наблюдения не переписываются."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        response = client.get(
            "/api/v1/showcase/transport-curve",
            params={"origin": "MOW", "observation_date": yesterday},
            headers=viewer_headers,
        )

        assert response.status_code == 200
        assert response.json()["observation_date"] == yesterday


class TestCoverageAndQuality:
    """Дыры должны быть видны глазом, а не вычитываться из графика."""

    def test_coverage_matrix_has_legend(self, client, viewer_headers):
        response = client.get("/api/v1/showcase/coverage", headers=viewer_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["legend"], "без словаря состояний матрица нечитаема"
        assert "NOT_OBSERVED" in body["legend"]
        assert body["thin_threshold"] > 0

    def test_quality_reports_the_plan_and_the_losses(self, client, viewer_headers):
        response = client.get("/api/v1/showcase/quality", headers=viewer_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["planned"] >= 0
        assert body["missing"] == max(0, body["planned"] - body["collected"])
        assert "stay_estimate_accuracy" in body
        for row in body["source_outcomes"]:
            assert row["meaning"], "технический исход нужно объяснить словами"
