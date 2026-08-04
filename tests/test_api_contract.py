"""Контракт API: авторизация, RBAC, error envelope, версионирование (DELTA §5)."""

from __future__ import annotations


class TestHealth:
    def test_health_reports_components(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code in (200, 503)
        body = response.json()
        names = {component["name"] for component in body["components"]}
        assert {"database", "result_cache", "raw_storage", "profiles", "sources"} <= names

    def test_live_needs_no_auth(self, client):
        assert client.get("/api/v1/health/live").status_code == 200

    def test_ready(self, client):
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 200
        assert response.json()["database"] is True

    def test_version_exposes_all_methodology_versions(self, client):
        body = client.get("/api/v1/version").json()
        assert {"app", "api", "engine", "normalization", "schema", "confidence_formula"} <= set(
            body["versions"]
        )
        assert body["metric_title"]
        assert body["disclaimer"]


class TestAuthentication:
    def test_rejects_anonymous(self, client):
        assert client.get("/api/v1/reference/cities").status_code == 401

    def test_rejects_wrong_password(self, client):
        response = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        )
        assert response.status_code == 401

    def test_unknown_user_indistinguishable_from_wrong_password(self, client):
        """Ответ не должен раскрывать существование учетной записи."""
        unknown = client.post(
            "/api/v1/auth/login", json={"username": "no-such-user", "password": "x"}
        )
        wrong = client.post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]

    def test_rejects_malformed_token(self, client):
        response = client.get(
            "/api/v1/reference/cities", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401

    def test_login_returns_role(self, client):
        body = client.post(
            "/api/v1/auth/login", json={"username": "analyst", "password": "analyst-pass-1"}
        ).json()
        assert body["role"] == "ANALYST"
        assert body["expires_in"] > 0

    def test_me(self, client, viewer_headers):
        body = client.get("/api/v1/auth/me", headers=viewer_headers).json()
        assert body["username"] == "viewer"
        assert body["role"] == "VIEWER"


class TestRbac:
    """Роли проверяются на backend, а не только скрытием элементов в UI."""

    def test_viewer_cannot_calculate(self, client, viewer_headers, scenario_payload):
        response = client.post(
            "/api/v1/calculations", json={"scenario": scenario_payload}, headers=viewer_headers
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    def test_viewer_cannot_read_audit(self, client, viewer_headers):
        assert client.get("/api/v1/audit/events", headers=viewer_headers).status_code == 403

    def test_viewer_can_read_dashboard(self, client, viewer_headers):
        assert client.get("/api/v1/dashboard/overview", headers=viewer_headers).status_code == 200

    def test_analyst_cannot_administer_sources(self, client, analyst_headers):
        response = client.post("/api/v1/sources/sandbox_alpha/disable", headers=analyst_headers)
        assert response.status_code == 403

    def test_analyst_can_create_scenario(self, client, analyst_headers, unique_scenario_payload):
        response = client.post(
            "/api/v1/scenarios", json=unique_scenario_payload, headers=analyst_headers
        )
        assert response.status_code in (200, 201)

    def test_admin_can_read_audit(self, client, admin_headers):
        assert client.get("/api/v1/audit/events", headers=admin_headers).status_code == 200


class TestErrorEnvelope:
    def test_not_found_shape(self, client, viewer_headers):
        response = client.get(
            "/api/v1/scenarios/00000000-0000-0000-0000-000000000000", headers=viewer_headers
        )
        assert response.status_code == 404
        error = response.json()["error"]
        assert set(error) == {"code", "message", "details", "request_id"}
        assert error["code"] == "NOT_FOUND"

    def test_validation_error_lists_fields(self, client, analyst_headers, scenario_payload):
        broken = dict(scenario_payload, adults=0)
        response = client.post("/api/v1/scenarios", json=broken, headers=analyst_headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert response.json()["error"]["details"]["fields"]

    def test_request_id_returned_and_echoed(self, client, viewer_headers):
        response = client.get("/api/v1/reference/cities", headers=viewer_headers)
        assert response.headers["X-Request-Id"]

        custom = client.get(
            "/api/v1/reference/cities",
            headers={**viewer_headers, "X-Request-Id": "my-trace-1"},
        )
        assert custom.headers["X-Request-Id"] == "my-trace-1"


class TestReference:
    def test_six_cities_of_mvp(self, client, viewer_headers):
        body = client.get("/api/v1/reference/cities", headers=viewer_headers).json()
        codes = {city["code"] for city in body["items"]}
        assert {"MOW", "LED", "KRR", "AER", "KGD", "UUS"} <= codes

    def test_fare_types_document_baggage_rule(self, client, viewer_headers):
        body = client.get("/api/v1/reference/fare-types", headers=viewer_headers).json()
        cheapest = next(item for item in body["items"] if item["value"] == "CHEAPEST")
        checked = next(item for item in body["items"] if item["value"] == "CHECKED_BAGGAGE")
        assert cheapest["requires_baggage_classification"] is False
        assert checked["requires_baggage_classification"] is True

    def test_constructor_meal_types_are_limited(self, client, viewer_headers):
        body = client.get("/api/v1/reference/meal-types", headers=viewer_headers).json()
        assert {item["value"] for item in body["items"]} == {"ANY", "NO_MEALS", "BREAKFAST"}

    def test_horizon_lists_sources(self, client, viewer_headers):
        body = client.get("/api/v1/reference/horizon", headers=viewer_headers).json()
        assert "transport" in body and "accommodation" in body


class TestOpenApi:
    def test_all_endpoints_under_v1(self, client):
        spec = client.get("/api/v1/openapi.json").json()
        assert all(path.startswith("/api/v1") for path in spec["paths"])

    def test_key_endpoints_present(self, client):
        paths = set(client.get("/api/v1/openapi.json").json()["paths"])
        for required in (
            "/api/v1/scenarios",
            "/api/v1/calculations",
            "/api/v1/market-snapshots",
            "/api/v1/market-snapshots/{snapshot_id}/recalculate",
            "/api/v1/scenario-runs/{run_id}/explain",
            "/api/v1/scenario-runs/{run_id}/source-breakdown",
            "/api/v1/scenario-runs/{run_id}/offers",
            "/api/v1/scenario-runs/{run_id}/rail-comparison",
            "/api/v1/dashboard/overview",
            "/api/v1/sources/{source_id}/confidence",
            "/api/v1/calculation-profiles",
            "/api/v1/jobs/{job_id}/events",
            "/api/v1/exports",
            "/api/v1/audit/events",
        ):
            assert required in paths, f"отсутствует {required}"
