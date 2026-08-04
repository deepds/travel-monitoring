"""Жизненный цикл сценария: создание вручную, наблюдение, удаление.

Ключевое различие, которое проверяется здесь: удаление записи каталога и
уничтожение накопленных наблюдений — разные операции. Первая обратима по
смыслу (историю можно читать дальше), вторая безвозвратна, потому что
источники не отдают цены задним числом.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest


@pytest.fixture()
def monitoring_payload(unique_scenario_payload: dict) -> dict:
    """Сценарий планового наблюдения — именно его заводит администратор."""
    payload = dict(unique_scenario_payload)
    payload["scenario_type"] = "MONITORING"
    return payload


def _create(client, headers, payload) -> dict:
    response = client.post("/api/v1/scenarios", json=payload, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()


class TestManualCreation:
    """Создание сценария из интерфейса администратора."""

    def test_created_scenario_is_active_and_listed(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        assert created["created"] is True
        assert created["is_active"] is True
        assert created["scenario_type"] == "MONITORING"

        listed = client.get(
            "/api/v1/scenarios", params={"search": created["code"]}, headers=admin_headers
        ).json()
        assert [item["id"] for item in listed["items"]] == [created["id"]]

    def test_name_is_derived_when_not_given(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        assert created["name"], "название должно собираться из маршрута и дат"
        assert created["code"], "код обязателен: по нему сценарий ищут в каталоге"

    def test_repeated_parameters_return_existing(self, client, admin_headers, monitoring_payload):
        first = _create(client, admin_headers, monitoring_payload)

        response = client.post("/api/v1/scenarios", json=monitoring_payload, headers=admin_headers)

        assert response.status_code == 200, "дубль не создается, возвращается существующий"
        assert response.json()["created"] is False
        assert response.json()["id"] == first["id"]

    def test_viewer_cannot_create(self, client, viewer_headers, monitoring_payload):
        response = client.post("/api/v1/scenarios", json=monitoring_payload, headers=viewer_headers)

        assert response.status_code == 403


class TestCreationValidation:
    """Невалидный сценарий отклоняется до попадания в каталог."""

    def test_same_cities_rejected(self, client, admin_headers, monitoring_payload):
        payload = dict(monitoring_payload, destination_city_code=monitoring_payload["origin_city_code"])

        assert client.post("/api/v1/scenarios", json=payload, headers=admin_headers).status_code == 422

    def test_return_before_departure_rejected(self, client, admin_headers, monitoring_payload):
        departure = date.fromisoformat(monitoring_payload["departure_date"])
        payload = dict(monitoring_payload, return_date=(departure - timedelta(days=1)).isoformat())

        assert client.post("/api/v1/scenarios", json=payload, headers=admin_headers).status_code == 422

    def test_rail_without_class_rejected(self, client, admin_headers, monitoring_payload):
        payload = dict(monitoring_payload, transport_type="RAIL", flight_fare_type=None, rail_class=None)

        assert client.post("/api/v1/scenarios", json=payload, headers=admin_headers).status_code == 422

    def test_unknown_city_rejected(self, client, admin_headers, monitoring_payload):
        payload = dict(monitoring_payload, destination_city_code="ZZZ")

        assert client.post(
            "/api/v1/scenarios", json=payload, headers=admin_headers
        ).status_code in (404, 422)


class TestObservationSwitch:
    """Признак активности управляет попаданием в плановый сбор."""

    def test_deactivated_scenario_leaves_schedule(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        client.post(f"/api/v1/scenarios/{created['id']}/deactivate", headers=admin_headers)
        after = client.get(f"/api/v1/scenarios/{created['id']}", headers=admin_headers).json()

        assert after["is_active"] is False
        active = client.get(
            "/api/v1/scenarios", params={"is_active": True, "page_size": 200}, headers=admin_headers
        ).json()
        assert created["id"] not in [item["id"] for item in active["items"]]

    def test_activation_returns_to_schedule(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)
        client.post(f"/api/v1/scenarios/{created['id']}/deactivate", headers=admin_headers)

        client.post(f"/api/v1/scenarios/{created['id']}/activate", headers=admin_headers)

        after = client.get(f"/api/v1/scenarios/{created['id']}", headers=admin_headers).json()
        assert after["is_active"] is True

    def test_analyst_cannot_switch(self, client, analyst_headers, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        response = client.post(
            f"/api/v1/scenarios/{created['id']}/deactivate", headers=analyst_headers
        )

        assert response.status_code == 403


class TestFootprint:
    """Перед удалением показывается объем накопленного."""

    def test_new_scenario_has_empty_footprint(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        data = client.get(
            f"/api/v1/scenarios/{created['id']}/footprint", headers=admin_headers
        ).json()

        assert data["scenario_code"] == created["code"]
        assert data == {
            "scenario_code": created["code"],
            "snapshots": 0,
            "runs": 0,
            "offers": 0,
            "raw_responses": 0,
            "html_snapshots": 0,
        }

    def test_footprint_is_admin_only(self, client, admin_headers, analyst_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        response = client.get(
            f"/api/v1/scenarios/{created['id']}/footprint", headers=analyst_headers
        )

        assert response.status_code == 403


class TestDeletion:
    """Два режима удаления с разной судьбой накопленных данных."""

    def test_soft_delete_keeps_history(self, client, admin_headers, monitoring_payload, sandbox_profile):
        created = _create(client, admin_headers, monitoring_payload)
        client.post(
            "/api/v1/calculations",
            json={"scenario_id": created["id"]},
            headers=admin_headers,
        )
        before = client.get(
            f"/api/v1/scenarios/{created['id']}/footprint", headers=admin_headers
        ).json()

        response = client.delete(f"/api/v1/scenarios/{created['id']}", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["purged"] is False
        # Сценарий скрыт из каталога, но сам объект и его история на месте.
        listed = client.get(
            "/api/v1/scenarios", params={"search": created["code"]}, headers=admin_headers
        ).json()
        assert listed["items"] == []
        after = client.get(
            f"/api/v1/scenarios/{created['id']}/footprint", headers=admin_headers
        ).json()
        assert after["snapshots"] == before["snapshots"]
        assert after["runs"] == before["runs"]

    def test_purge_removes_scenario_and_data(
        self, client, admin_headers, monitoring_payload, sandbox_profile
    ):
        created = _create(client, admin_headers, monitoring_payload)
        client.post(
            "/api/v1/calculations",
            json={"scenario_id": created["id"]},
            headers=admin_headers,
        )

        response = client.delete(
            f"/api/v1/scenarios/{created['id']}",
            params={"purge_data": True},
            headers=admin_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["purged"] is True
        assert set(body["removed"]) >= {"snapshots", "runs", "offers"}
        # Записи больше нет вовсе — ни в каталоге, ни по прямой ссылке.
        assert client.get(
            f"/api/v1/scenarios/{created['id']}", headers=admin_headers
        ).status_code == 404
        assert client.get(
            "/api/v1/scenarios",
            params={"search": created["code"], "include_deleted": True},
            headers=admin_headers,
        ).json()["items"] == []

    def test_purge_reports_what_was_destroyed(
        self, client, admin_headers, monitoring_payload, sandbox_profile
    ):
        created = _create(client, admin_headers, monitoring_payload)
        client.post(
            "/api/v1/calculations", json={"scenario_id": created["id"]}, headers=admin_headers
        )
        expected = client.get(
            f"/api/v1/scenarios/{created['id']}/footprint", headers=admin_headers
        ).json()

        removed = client.delete(
            f"/api/v1/scenarios/{created['id']}",
            params={"purge_data": True},
            headers=admin_headers,
        ).json()["removed"]

        for key in ("snapshots", "runs", "offers"):
            assert removed[key] == expected[key], f"расхождение по «{key}»"

    def test_repeated_soft_delete_is_idempotent(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)
        client.delete(f"/api/v1/scenarios/{created['id']}", headers=admin_headers)

        response = client.delete(f"/api/v1/scenarios/{created['id']}", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_analyst_cannot_delete(self, client, admin_headers, analyst_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        response = client.delete(f"/api/v1/scenarios/{created['id']}", headers=analyst_headers)

        assert response.status_code == 403

    def test_deletion_is_audited(self, client, admin_headers, monitoring_payload):
        created = _create(client, admin_headers, monitoring_payload)

        client.delete(
            f"/api/v1/scenarios/{created['id']}",
            params={"purge_data": True},
            headers=admin_headers,
        )

        events = client.get(
            "/api/v1/audit/events",
            params={"action": "SCENARIO_DELETE", "page_size": 50},
            headers=admin_headers,
        ).json()
        assert any(
            event["object_id"] == created["id"] for event in events["items"]
        ), "удаление данных обязано оставлять след в аудите"

    def test_missing_scenario_gives_404(self, client, admin_headers):
        response = client.delete(f"/api/v1/scenarios/{uuid.uuid4()}", headers=admin_headers)

        assert response.status_code == 404
