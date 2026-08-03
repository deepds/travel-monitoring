"""Сквозной конвейер: сценарий → снимок → расчет → пересчет (DELTA §1, §11.1)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def calculated(client, analyst_headers, unique_scenario_payload, sandbox_profile):
    """Выполненный расчет по свежему сценарию."""
    response = client.post(
        "/api/v1/calculations",
        json={"scenario": unique_scenario_payload},
        headers=analyst_headers,
    )
    assert response.status_code in (200, 202), response.text
    body = response.json()

    if body.get("cached") and body.get("run"):
        return body["run"]

    job = client.get(f"/api/v1/calculations/{body['job_id']}", headers=analyst_headers).json()
    assert job["status"] == "SUCCESS", job.get("error_message")
    assert job.get("run"), "расчет не вернул ScenarioRun"
    return job["run"]


class TestCalculation:
    def test_produces_complete_result(self, calculated):
        run = calculated
        assert run["status"] in ("SUCCESS", "PARTIAL_SUCCESS")
        assert run["total_estimated_cost"] is not None
        assert run["price_per_person"] is not None
        assert run["currency"] == "RUB"

    def test_percentiles_are_ordered(self, calculated):
        for component in ("transport", "accommodation"):
            stats = calculated[component]
            if stats["median"] is None:
                continue
            assert stats["p25"] <= stats["median"] <= stats["p75"]

    def test_total_is_sum_of_component_medians(self, calculated):
        """Итог — сумма компонентных медиан (SCOPE-R P §10)."""
        run = calculated
        transport = run["transport"]["median"]
        hotel = run["accommodation"]["median"]
        if transport is None or hotel is None:
            pytest.skip("компонент отсутствует")
        assert run["total_estimated_cost"] == pytest.approx(transport + hotel, rel=0.01)

    def test_price_per_person_matches_traveler_count(self, calculated):
        run = calculated
        expected = run["total_estimated_cost"] / run["traveler_count"]
        assert run["price_per_person"] == pytest.approx(expected, rel=0.01)

    def test_records_methodology_versions(self, calculated):
        versions = calculated["versions"]
        assert versions["engine"] and versions["normalization"] and versions["profile"]

    def test_synthetic_data_is_flagged(self, calculated):
        """Песочница обязана быть видна в результате, а не выдаваться за рынок."""
        assert calculated["contains_synthetic_data"] is True

    def test_quality_score_within_range(self, calculated):
        assert 0.0 <= calculated["quality_score"] <= 100.0

    def test_confidence_has_explanation(self, calculated):
        assert calculated["confidence_level"] in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")
        assert calculated["confidence"]["reason"]


class TestCache:
    def test_second_identical_request_hits_cache(
        self, client, analyst_headers, unique_scenario_payload, sandbox_profile
    ):
        first = client.post(
            "/api/v1/calculations",
            json={"scenario": unique_scenario_payload},
            headers=analyst_headers,
        )
        assert first.status_code in (200, 202)

        second = client.post(
            "/api/v1/calculations",
            json={"scenario": unique_scenario_payload},
            headers=analyst_headers,
        )
        assert second.status_code == 200
        assert second.json()["cached"] is True
        assert second.json()["run"]["id"]

    def test_force_refresh_bypasses_cache(
        self, client, analyst_headers, unique_scenario_payload, sandbox_profile
    ):
        client.post(
            "/api/v1/calculations",
            json={"scenario": unique_scenario_payload},
            headers=analyst_headers,
        )
        forced = client.post(
            "/api/v1/calculations",
            json={"scenario": unique_scenario_payload, "force_refresh": True},
            headers=analyst_headers,
        )
        assert forced.json().get("cached") is False


class TestScenarioIdentity:
    def test_same_parameters_produce_same_scenario(
        self, client, analyst_headers, unique_scenario_payload
    ):
        """Уникальная комбинация параметров имеет стабильный отпечаток."""
        first = client.post(
            "/api/v1/scenarios", json=unique_scenario_payload, headers=analyst_headers
        ).json()
        second = client.post(
            "/api/v1/scenarios", json=unique_scenario_payload, headers=analyst_headers
        ).json()
        assert first["id"] == second["id"]
        assert first["fingerprint"] == second["fingerprint"]
        assert second["created"] is False

    def test_different_parameters_produce_different_fingerprint(
        self, client, analyst_headers, unique_scenario_payload
    ):
        first = client.post(
            "/api/v1/scenarios", json=unique_scenario_payload, headers=analyst_headers
        ).json()
        variant = dict(unique_scenario_payload, stars="5")
        second = client.post(
            "/api/v1/scenarios", json=variant, headers=analyst_headers
        ).json()
        assert first["fingerprint"] != second["fingerprint"]


class TestSnapshot:
    def test_snapshot_holds_offers_and_raw_artifacts(self, client, analyst_headers, calculated):
        snapshot_id = calculated["market_snapshot_id"]
        assert snapshot_id

        snapshot = client.get(
            f"/api/v1/market-snapshots/{snapshot_id}", headers=analyst_headers
        ).json()
        assert snapshot["status"] in ("COMPLETE", "PARTIAL")
        assert snapshot["transport_offer_count"] + snapshot["accommodation_offer_count"] > 0

        offers = client.get(
            f"/api/v1/market-snapshots/{snapshot_id}/offers", headers=analyst_headers
        ).json()
        assert offers["meta"]["total"] > 0

        artifacts = client.get(
            f"/api/v1/market-snapshots/{snapshot_id}/raw-artifacts", headers=analyst_headers
        ).json()
        assert artifacts["counts"]["raw_responses"] > 0

    def test_source_results_survive_for_explainability(
        self, client, analyst_headers, calculated
    ):
        sources = client.get(
            f"/api/v1/market-snapshots/{calculated['market_snapshot_id']}/sources",
            headers=analyst_headers,
        ).json()
        assert sources["items"]
        for row in sources["items"]:
            assert row["outcome"]

    def test_one_snapshot_serves_several_runs(self, client, analyst_headers, calculated):
        """Пересчет другим профилем не меняет снимок и создает новый run."""
        snapshot_id = calculated["market_snapshot_id"]
        before = client.get(
            f"/api/v1/market-snapshots/{snapshot_id}", headers=analyst_headers
        ).json()

        profiles = client.get("/api/v1/calculation-profiles", headers=analyst_headers).json()
        other = next(
            (p for p in profiles["items"] if p["id"] != before["runs"][0]["id"]),
            profiles["items"][0],
        )

        response = client.post(
            f"/api/v1/market-snapshots/{snapshot_id}/recalculate",
            json={"profile_id": other["id"]},
            headers=analyst_headers,
        )
        assert response.status_code in (200, 202), response.text

        after = client.get(
            f"/api/v1/market-snapshots/{snapshot_id}", headers=analyst_headers
        ).json()
        assert after["run_count"] > before["run_count"]
        # Снимок неизменяем: счетчики предложений те же.
        assert after["transport_offer_count"] == before["transport_offer_count"]
        assert after["accommodation_offer_count"] == before["accommodation_offer_count"]


class TestExplainability:
    def test_explain_lists_selection_and_quality(self, client, analyst_headers, calculated):
        body = client.get(
            f"/api/v1/scenario-runs/{calculated['id']}/explain", headers=analyst_headers
        ).json()
        assert body["explainability"]
        assert body["quality"]["breakdown"]
        assert body["versions"]["profile"]
        assert body["disclaimer"]

    def test_source_breakdown_explains_ineligibility(
        self, client, analyst_headers, calculated
    ):
        body = client.get(
            f"/api/v1/scenario-runs/{calculated['id']}/source-breakdown", headers=analyst_headers
        ).json()
        rows = body.get("rows", [])
        assert rows
        for row in rows:
            assert "eligible" in row
            if not row["eligible"]:
                # Недопуск обязан быть объяснен, а не выглядеть как отсутствие данных.
                assert row["ineligibility_reasons"]


class TestRunImmutability:
    def test_run_is_not_modified_by_later_recalculation(
        self, client, analyst_headers, calculated
    ):
        run_id = calculated["id"]
        before = client.get(f"/api/v1/scenario-runs/{run_id}", headers=analyst_headers).json()

        client.post(
            f"/api/v1/market-snapshots/{calculated['market_snapshot_id']}/recalculate",
            json={},
            headers=analyst_headers,
        )

        after = client.get(f"/api/v1/scenario-runs/{run_id}", headers=analyst_headers).json()
        assert after["total_estimated_cost"] == before["total_estimated_cost"]
        assert after["quality_score"] == before["quality_score"]
        assert after["versions"] == before["versions"]
