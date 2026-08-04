"""Профили расчета, источники и Source Confidence (DELTA §6.12, §7)."""

from __future__ import annotations

import pytest


class TestProfileLifecycle:
    """DRAFT → ACTIVE → ARCHIVED; активная версия неизменяема (SCOPE-R R §3)."""

    def test_active_profile_cannot_be_edited(self, client, admin_headers):
        profiles = client.get("/api/v1/calculation-profiles", headers=admin_headers).json()
        active = next((p for p in profiles["items"] if p["status"] == "ACTIVE"), None)
        if active is None:
            pytest.skip("нет активного профиля")

        response = client.patch(
            f"/api/v1/calculation-profiles/{active['id']}",
            json={"name": "Попытка изменить активную версию"},
            headers=admin_headers,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROFILE_IMMUTABLE"

    def test_clone_creates_new_draft_version(self, client, admin_headers):
        profiles = client.get("/api/v1/calculation-profiles", headers=admin_headers).json()
        source = profiles["items"][0]

        response = client.post(
            f"/api/v1/calculation-profiles/{source['id']}/clone", json={}, headers=admin_headers
        )
        assert response.status_code == 201
        clone = response.json()
        assert clone["status"] == "DRAFT"
        assert clone["code"] == source["code"]
        assert clone["version"] != source["version"]
        assert clone["version_seq"] > source["version_seq"]

    def test_draft_can_be_edited(self, client, admin_headers):
        profiles = client.get("/api/v1/calculation-profiles", headers=admin_headers).json()
        base = profiles["items"][0]
        clone = client.post(
            f"/api/v1/calculation-profiles/{base['id']}/clone", json={}, headers=admin_headers
        ).json()

        response = client.patch(
            f"/api/v1/calculation-profiles/{clone['id']}",
            json={"description": "Уточнена методика выбросов"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["description"] == "Уточнена методика выбросов"

    def test_activation_archives_previous_version(self, client, admin_headers):
        profiles = client.get(
            "/api/v1/calculation-profiles", params={"code": "baseline"}, headers=admin_headers
        ).json()
        if not profiles["items"]:
            pytest.skip("профиль baseline отсутствует")

        base = profiles["items"][0]
        clone = client.post(
            f"/api/v1/calculation-profiles/{base['id']}/clone", json={}, headers=admin_headers
        ).json()

        activated = client.post(
            f"/api/v1/calculation-profiles/{clone['id']}/activate", headers=admin_headers
        )
        assert activated.status_code == 200
        assert activated.json()["status"] == "ACTIVE"

        # Прежняя версия того же кода больше не активна.
        after = client.get(
            "/api/v1/calculation-profiles", params={"code": "baseline"}, headers=admin_headers
        ).json()
        active = [p for p in after["items"] if p["status"] == "ACTIVE"]
        assert len(active) == 1
        assert active[0]["id"] == clone["id"]

    def test_rules_schema_is_exposed(self, client, analyst_headers):
        profiles = client.get("/api/v1/calculation-profiles", headers=analyst_headers).json()
        schema = client.get(
            f"/api/v1/calculation-profiles/{profiles['items'][0]['id']}/rules-schema",
            headers=analyst_headers,
        ).json()
        assert "properties" in schema
        assert {"filters", "eligibility", "outliers", "aggregation", "quality"} <= set(
            schema["properties"]
        )


class TestProfileRules:
    """Схема правил не должна принимать бессмысленные комбинации."""

    def test_hard_timeout_below_soft_is_rejected(self):
        from pydantic import ValidationError as PydanticError

        from tco.schemas.profile import ProfileRules

        with pytest.raises(PydanticError):
            ProfileRules.parse(
                {"limits": {"source_soft_timeout_seconds": 30, "source_hard_timeout_seconds": 10}}
            )

    def test_inverted_confidence_thresholds_rejected(self):
        from pydantic import ValidationError as PydanticError

        from tco.schemas.profile import ProfileRules

        with pytest.raises(PydanticError):
            ProfileRules.parse({"confidence": {"high_min_quality": 50, "medium_min_quality": 80}})

    def test_source_cannot_be_allowed_and_excluded(self):
        from pydantic import ValidationError as PydanticError

        from tco.schemas.profile import ProfileRules

        with pytest.raises(PydanticError):
            ProfileRules.parse(
                {"allowed_source_codes": ["a", "b"], "excluded_source_codes": ["b"]}
            )

    def test_defaults_match_specification(self):
        """Стартовые пороги допуска источника (SCOPE-R P §8)."""
        from tco.schemas.profile import ProfileRules

        rules = ProfileRules.parse({})
        assert rules.eligibility.min_valid_offers == 5
        assert rules.eligibility.max_data_age_minutes == 60
        assert rules.eligibility.max_invalid_offer_ratio == 0.30
        assert rules.eligibility.max_unclassified_fare_ratio == 0.40
        assert rules.outliers.iqr_multiplier == 1.5
        assert rules.quality.partial_success_threshold == 60.0

    def test_quality_weights_sum_to_one(self):
        from tco.schemas.profile import ProfileRules

        weights = ProfileRules.parse({}).quality.weights
        total = (
            weights.component_completeness
            + weights.source_count
            + weights.offer_count
            + weights.source_agreement
            + weights.freshness
            + weights.connector_stability
        )
        assert total == pytest.approx(1.0)

    def test_unknown_field_is_rejected(self):
        """Незнакомое поле не должно молча игнорироваться."""
        from pydantic import ValidationError as PydanticError

        from tco.schemas.profile import ProfileRules

        with pytest.raises(PydanticError):
            ProfileRules.parse({"unknown_setting": 1})


class TestSources:
    def test_list_contains_both_categories(self, client, viewer_headers):
        body = client.get("/api/v1/sources", headers=viewer_headers).json()
        categories = {item["category"] for item in body["items"]}
        assert {"TRANSPORT", "ACCOMMODATION"} <= categories

    def test_synthetic_source_is_flagged(self, client, viewer_headers):
        body = client.get("/api/v1/sources", headers=viewer_headers).json()
        sandbox = [item for item in body["items"] if item["code"].startswith("sandbox")]
        assert sandbox
        assert all(item["is_synthetic"] for item in sandbox)

    def test_enable_disable_is_audited(self, client, admin_headers):
        client.post("/api/v1/sources/sandbox_alpha/disable", headers=admin_headers)
        client.post("/api/v1/sources/sandbox_alpha/enable", headers=admin_headers)

        audit = client.get(
            "/api/v1/audit/events",
            params={"object_type": "Source", "page_size": 20},
            headers=admin_headers,
        ).json()
        actions = {event["action"] for event in audit["items"]}
        assert {"SOURCE_ENABLE", "SOURCE_DISABLE"} & actions

    def test_confidence_is_versioned_and_explainable(self, client, admin_headers):
        recalculated = client.post(
            "/api/v1/sources/sandbox_alpha/confidence/recalculate", headers=admin_headers
        )
        assert recalculated.status_code == 200
        body = recalculated.json()
        assert 0 <= body["score"] <= 100
        assert body["level"] in ("HIGH", "MEDIUM", "LOW", "UNTRUSTED")
        assert body["formula_version"]
        # Объяснимость обязательна: одного числа недостаточно.
        assert body["factor_scores"]
        assert body["input_metrics"] is not None

    def test_manual_override_is_recorded(self, client, admin_headers):
        client.post("/api/v1/sources/sandbox_beta/confidence/recalculate", headers=admin_headers)
        response = client.post(
            "/api/v1/sources/sandbox_beta/confidence/override",
            json={"score": 42.0, "reason": "Ручная проверка challenge set"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["manual_override"] == 42.0
        assert body["effective_score"] == 42.0
        # Исходный расчет сохраняется рядом с ручным значением.
        assert body["score"] != 42.0 or body["override_reason"]
        assert body["approved_by"] == "admin"

    def test_override_requires_reason(self, client, admin_headers):
        response = client.post(
            "/api/v1/sources/sandbox_beta/confidence/override",
            json={"score": 50.0, "reason": ""},
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_confidence_levels_documented(self, client, viewer_headers):
        body = client.get("/api/v1/sources/sandbox_alpha/confidence", headers=viewer_headers).json()
        assert body["levels"] == {
            "HIGH": "80–100",
            "MEDIUM": "60–79",
            "LOW": "40–59",
            "UNTRUSTED": "0–39",
        }


class TestExport:
    def test_viewer_may_export(self, client, viewer_headers):
        """Выгрузка своих наблюдений — обычная работа бизнес-пользователя.

        Подотчетность обеспечивает запись в аудит, а не запрет на действие.
        """
        created = client.post(
            "/api/v1/exports",
            json={"dataset": "SCENARIO_RUNS", "format": "CSV"},
            headers=viewer_headers,
        )
        assert created.status_code in (200, 202), created.text

    def test_csv_export_contains_quality_fields(self, client, analyst_headers):
        created = client.post(
            "/api/v1/exports",
            json={"dataset": "SCENARIO_RUNS", "format": "CSV"},
            params={"include_synthetic": True},
            headers=analyst_headers,
        )
        assert created.status_code in (200, 202)
        job_id = created.json()["job_id"]

        download = client.get(f"/api/v1/exports/{job_id}/download", headers=analyst_headers)
        assert download.status_code == 200
        text = download.content.decode("utf-8-sig")
        header = text.splitlines()[0]
        assert "Quality Score" in header
        assert "Статус" in header
        assert "Профиль" in header or "профиль" in header

    def test_xlsx_export_is_valid_workbook(self, client, analyst_headers):
        created = client.post(
            "/api/v1/exports",
            json={"dataset": "SCENARIO_RUNS", "format": "XLSX"},
            params={"include_synthetic": True},
            headers=analyst_headers,
        )
        job_id = created.json()["job_id"]
        download = client.get(f"/api/v1/exports/{job_id}/download", headers=analyst_headers)
        assert download.status_code == 200
        # XLSX — это ZIP-контейнер.
        assert download.content[:2] == b"PK"
