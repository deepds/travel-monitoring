"""Обязательные сценарии отказов (SCOPE-R E §5).

Проверяется, что деградация управляемая: платформа не подменяет отсутствующие
данные, не обрушивается из-за одного источника и честно сообщает о неполноте.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tco.core.enums import ConnectorOutcome, ValidityStatus
from tco.engine.aggregation import SourceCollectionInfo, check_eligibility, combine_across_sources
from tco.engine.statistics import median
from tco.schemas.profile import ProfileRules


class TestUnsupportedScenario:
    """Невалидный сценарий завершается до обращения к внешним источникам."""

    def test_same_origin_and_destination(self, client, analyst_headers, scenario_payload):
        payload = dict(scenario_payload, destination_city_code=scenario_payload["origin_city_code"])
        response = client.post("/api/v1/scenarios", json=payload, headers=analyst_headers)
        assert response.status_code == 422

    def test_return_before_departure(self, client, analyst_headers, scenario_payload):
        payload = dict(
            scenario_payload,
            return_date=(date.fromisoformat(scenario_payload["departure_date"]) - timedelta(days=1)).isoformat(),
        )
        response = client.post("/api/v1/scenarios", json=payload, headers=analyst_headers)
        assert response.status_code == 422

    def test_past_departure_date_is_rejected_by_calculation(
        self, client, analyst_headers, scenario_payload
    ):
        past = date.today() - timedelta(days=10)
        payload = dict(
            scenario_payload,
            departure_date=past.isoformat(),
            return_date=(past + timedelta(days=3)).isoformat(),
        )
        response = client.post(
            "/api/v1/calculations", json={"scenario": payload}, headers=analyst_headers
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] in ("SCENARIO_UNSUPPORTED", "VALIDATION_ERROR")

    def test_rail_without_class_is_rejected(self, client, analyst_headers, scenario_payload):
        payload = dict(scenario_payload, transport_type="RAIL", flight_fare_type=None, rail_class=None)
        response = client.post("/api/v1/scenarios", json=payload, headers=analyst_headers)
        assert response.status_code == 422

    def test_unknown_city_is_rejected(self, client, analyst_headers, scenario_payload):
        payload = dict(scenario_payload, destination_city_code="XXX")
        response = client.post("/api/v1/scenarios", json=payload, headers=analyst_headers)
        assert response.status_code in (404, 422)


class TestSourceEligibility:
    """Источник не допускается к расчету при недостаточном качестве данных."""

    @staticmethod
    def _info(**overrides) -> SourceCollectionInfo:
        from tco.core.utils import utcnow

        defaults = dict(
            source_code="test_source",
            source_name="Тестовый источник",
            outcome=ConnectorOutcome.SUCCESS,
            collected_at=utcnow(),
        )
        defaults.update(overrides)
        return SourceCollectionInfo(**defaults)

    @staticmethod
    def _offers(count: int, *, valid: bool = True):
        """Предложения нужной валидности.

        Используется настоящая ORM-модель (без сохранения в БД), чтобы тест
        проверял тот же контракт, что и рабочий код.
        """
        from tco.db.models.offer import Offer

        status = ValidityStatus.VALID.value if valid else ValidityStatus.INVALID_PRICE.value
        return [
            Offer(validity_status=status, classification_status="CLASSIFIED")
            for _ in range(count)
        ]

    def test_failed_call_is_not_eligible(self):
        rules = ProfileRules.parse({})
        eligible, reasons, _ = check_eligibility(
            self._info(outcome=ConnectorOutcome.TIMEOUT), self._offers(10), self._offers(10), rules
        )
        assert eligible is False
        assert any("неуспешно" in reason for reason in reasons)

    def test_too_few_offers_is_not_eligible(self):
        rules = ProfileRules.parse({})
        eligible, reasons, _ = check_eligibility(
            self._info(), self._offers(1), self._offers(1), rules
        )
        assert eligible is False
        assert reasons

    def test_stale_data_is_not_eligible(self):
        from datetime import timedelta as td

        from tco.core.utils import utcnow

        rules = ProfileRules.parse({})
        stale = self._info(collected_at=utcnow() - td(hours=6))
        eligible, reasons, age = check_eligibility(stale, self._offers(20), self._offers(20), rules)
        assert eligible is False
        assert any("устарели" in reason for reason in reasons)
        assert age is not None and age > 60

    def test_high_invalid_ratio_is_not_eligible(self):
        """Доля невалидных записей выше порога закрывает источнику допуск."""
        rules = ProfileRules.parse({})
        offers = self._offers(6) + self._offers(14, valid=False)
        eligible, reasons, _ = check_eligibility(self._info(), offers, offers, rules)
        assert eligible is False
        assert any("невалидных" in reason for reason in reasons)

    def test_good_source_is_eligible(self):
        rules = ProfileRules.parse({})
        offers = self._offers(20)
        eligible, reasons, _ = check_eligibility(self._info(), offers, offers, rules)
        assert eligible is True
        assert reasons == []


class TestCrossSourceCombination:
    """Правила межисточниковой агрегации (SCOPE-R P §9)."""

    def test_single_source_is_marked(self):
        rules = ProfileRules.parse({})
        value, method = combine_across_sources([100.0], rules)
        assert value == 100.0
        assert method == "SINGLE_SOURCE"

    def test_two_sources_are_averaged(self):
        rules = ProfileRules.parse({})
        value, method = combine_across_sources([100.0, 200.0], rules)
        assert value == 150.0
        assert method == "MEAN_OF_TWO"

    def test_three_sources_use_median_of_medians(self):
        rules = ProfileRules.parse({})
        value, method = combine_across_sources([100.0, 150.0, 400.0], rules)
        assert value == 150.0
        assert method == "MEDIAN_OF_MEDIANS"

    def test_no_sources_gives_nothing(self):
        rules = ProfileRules.parse({})
        value, method = combine_across_sources([], rules)
        assert value is None
        assert method == "NONE"

    def test_outlier_source_does_not_drag_median(self):
        """Медиана медиан устойчива к одному сильно отклоняющемуся источнику."""
        rules = ProfileRules.parse({})
        normal, _ = combine_across_sources([100.0, 110.0, 105.0], rules)
        with_outlier, _ = combine_across_sources([100.0, 110.0, 105.0, 10000.0], rules)
        assert abs(with_outlier - normal) < normal * 0.5


class TestMissingComponent:
    """Отсутствующий компонент не подменяется старым значением."""

    def test_total_absent_when_component_missing(self, session):
        from tco.db.models.run import ScenarioRun

        run = ScenarioRun(
            transport_median=None,
            hotel_median=100,
            total_estimated_cost=None,
        )
        assert run.is_complete is False

    def test_median_of_empty_is_none(self):
        assert median([]) is None


class TestConnectorIsolation:
    """Падение одного источника не обрушивает расчет остальных."""

    def test_partial_snapshot_still_produces_run(
        self, client, analyst_headers, unique_scenario_payload, sandbox_profile
    ):
        response = client.post(
            "/api/v1/calculations",
            json={"scenario": unique_scenario_payload},
            headers=analyst_headers,
        )
        assert response.status_code in (200, 202)
        body = response.json()
        run = body.get("run")
        if not run:
            job = client.get(
                f"/api/v1/calculations/{body['job_id']}", headers=analyst_headers
            ).json()
            run = job.get("run")

        assert run is not None, "расчет должен состояться даже при сбое части источников"

        snapshot = client.get(
            f"/api/v1/market-snapshots/{run['market_snapshot_id']}/sources",
            headers=analyst_headers,
        ).json()
        outcomes = {row["outcome"] for row in snapshot["items"]}
        # На стенде часть внешних источников недоступна — это штатная ситуация.
        assert "SUCCESS" in outcomes


class TestJobFailureHandling:
    def test_cancel_of_finished_job_conflicts(self, client, admin_headers):
        jobs = client.get("/api/v1/jobs?page_size=50", headers=admin_headers).json()
        terminal = [
            job for job in jobs["items"]
            if job["status"] in ("SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT", "PARTIAL")
        ]
        if not terminal:
            pytest.skip("нет завершенных задач")
        response = client.post(
            f"/api/v1/jobs/{terminal[0]['job_id']}/cancel", headers=admin_headers
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    def test_unknown_job_is_404(self, client, admin_headers):
        response = client.get(
            "/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=admin_headers
        )
        assert response.status_code == 404


class TestExportSafety:
    def test_csv_injection_is_neutralized(self):
        """Формулы в экспорте не должны исполняться в табличном редакторе."""
        from tco.core.utils import csv_safe

        for dangerous in ("=1+1", "+cmd", "-cmd", "@SUM(A1)"):
            assert not str(csv_safe(dangerous)).startswith(("=", "+", "-", "@"))

    def test_plain_text_untouched(self):
        from tco.core.utils import csv_safe

        assert csv_safe("Москва → Сочи") == "Москва → Сочи"


class TestSecrets:
    def test_logging_redacts_sensitive_keys(self):
        from tco.core.logging import redact

        payload = {
            "authorization": "Bearer secret",
            "nested": {"api_key": "abc", "password": "p"},
            "safe": "значение",
        }
        cleaned = redact(payload)
        assert cleaned["authorization"] == "***"
        assert cleaned["nested"]["api_key"] == "***"
        assert cleaned["nested"]["password"] == "***"
        assert cleaned["safe"] == "значение"
