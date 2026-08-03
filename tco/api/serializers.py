"""Преобразование ORM-объектов в представления API.

Вынесено отдельно, чтобы одна и та же сущность выглядела одинаково во всех
эндпоинтах, а изменение контракта было заметно в одном месте
(``docs/DATA_CONTRACT.md``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tco.db.models.job import AuditEvent, ExportArtifact
from tco.db.models.offer import Offer
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City, ScenarioTemplate
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot, SnapshotSourceResult
from tco.db.models.source import Source, SourceConfidence, SourceMetric


def _money(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------------- #
# Сценарии
# --------------------------------------------------------------------------- #


def scenario_brief(scenario: TravelScenario) -> dict[str, Any]:
    return {
        "id": str(scenario.id),
        "code": scenario.code,
        "name": scenario.name,
        "scenario_type": scenario.scenario_type,
        "origin": _city_ref(scenario.origin_city),
        "destination": _city_ref(scenario.destination_city),
        "departure_date": _iso(scenario.departure_date),
        "return_date": _iso(scenario.return_date),
        "nights": scenario.nights,
        "transport_type": scenario.transport_type,
        "accommodation_type": scenario.accommodation_type,
        "stars": scenario.stars,
        "is_active": scenario.is_active,
    }


def scenario_full(scenario: TravelScenario) -> dict[str, Any]:
    return {
        **scenario_brief(scenario),
        "adults": scenario.adults,
        "children_ages": list(scenario.children_ages or []),
        "traveler_count": scenario.traveler_count,
        "flight_fare_type": scenario.flight_fare_type,
        "rail_class": scenario.rail_class,
        "meal_type": scenario.meal_type,
        "cancellation_filter": scenario.cancellation_filter,
        "calculation_profile_id": str(scenario.calculation_profile_id)
        if scenario.calculation_profile_id
        else None,
        "active_from": _iso(scenario.active_from),
        "active_until": _iso(scenario.active_until),
        "fingerprint": scenario.fingerprint,
        "priority": scenario.priority,
        "tags": list(scenario.tags or []),
        "notes": scenario.notes,
        "source_file": scenario.source_file,
        "deleted_at": _iso(scenario.deleted_at),
        "created_at": _iso(scenario.created_at),
        "updated_at": _iso(scenario.updated_at),
    }


def _city_ref(city: City | None) -> dict[str, Any] | None:
    if city is None:
        return None
    return {"id": str(city.id), "code": city.code, "name": city.name}


def template(item: ScenarioTemplate) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "defaults": item.defaults,
        "sort_order": item.sort_order,
        "is_active": item.is_active,
    }


# --------------------------------------------------------------------------- #
# Снимки рынка
# --------------------------------------------------------------------------- #


def snapshot_brief(snapshot: MarketSnapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "scenario_id": str(snapshot.scenario_id),
        "scenario_code": snapshot.scenario.code if snapshot.scenario else None,
        "snapshot_type": snapshot.snapshot_type,
        "status": snapshot.status,
        "observed_at": _iso(snapshot.observed_at),
        "observation_date": _iso(snapshot.observation_date),
        "observation_slot": snapshot.observation_slot,
        "completed_at": _iso(snapshot.completed_at),
        "source_codes": list(snapshot.source_codes or []),
        "transport_offer_count": snapshot.transport_offer_count,
        "accommodation_offer_count": snapshot.accommodation_offer_count,
        "valid_offer_count": snapshot.valid_offer_count,
        "contains_synthetic_data": snapshot.contains_synthetic_data,
        "offers_available": snapshot.offers_available,
    }


def snapshot_full(snapshot: MarketSnapshot) -> dict[str, Any]:
    return {
        **snapshot_brief(snapshot),
        "scenario_fingerprint": snapshot.scenario_fingerprint,
        "requested_at": _iso(snapshot.requested_at),
        "invalid_offer_count": snapshot.invalid_offer_count,
        "duplicate_offer_count": snapshot.duplicate_offer_count,
        "outlier_offer_count": snapshot.outlier_offer_count,
        "raw_response_count": len(snapshot.raw_response_refs or []),
        "html_snapshot_count": len(snapshot.html_snapshot_refs or []),
        "normalization_version": snapshot.normalization_version,
        "connector_versions": snapshot.connector_versions,
        "source_confidence_summary": snapshot.source_confidence_summary,
        "collection_summary": snapshot.collection_summary,
        "scenario_params": snapshot.scenario_params,
        "error_summary": list(snapshot.error_summary or []),
        "idempotency_key": snapshot.idempotency_key,
        "job_id": str(snapshot.job_id) if snapshot.job_id else None,
        "created_by": snapshot.created_by,
        "created_at": _iso(snapshot.created_at),
        "offers_purged_at": _iso(snapshot.offers_purged_at),
        "is_finalized": snapshot.is_finalized,
    }


def snapshot_source_result(row: SnapshotSourceResult) -> dict[str, Any]:
    return {
        "source_id": str(row.source_id),
        "source_code": row.source_code,
        "source_name": row.source_name,
        "offer_type": row.offer_type,
        "is_synthetic": row.is_synthetic,
        "outcome": row.outcome,
        "latency_ms": row.latency_ms,
        "attempts": row.attempts,
        "raw_offer_count": row.raw_offer_count,
        "normalized_offer_count": row.normalized_offer_count,
        "valid_offer_count": row.valid_offer_count,
        "invalid_offer_count": row.invalid_offer_count,
        "unclassified_offer_count": row.unclassified_offer_count,
        "duplicate_offer_count": row.duplicate_offer_count,
        "required_field_completeness": row.required_field_completeness,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "connector_version": row.connector_version,
        "collected_at": _iso(row.collected_at),
    }


# --------------------------------------------------------------------------- #
# Расчеты
# --------------------------------------------------------------------------- #


def run_brief(run: ScenarioRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "scenario_id": str(run.scenario_id),
        "scenario_code": run.scenario.code if run.scenario else None,
        "scenario_name": run.scenario.name if run.scenario else None,
        "market_snapshot_id": str(run.market_snapshot_id) if run.market_snapshot_id else None,
        "run_type": run.run_type,
        "status": run.status,
        "component_statuses": list(run.component_statuses or []),
        "observation_date": _iso(run.observation_date),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "lead_time_days": run.lead_time_days,
        "total_estimated_cost": _money(run.total_estimated_cost),
        "price_per_person": _money(run.price_per_person),
        "currency": run.currency,
        "quality_score": run.quality_score,
        "confidence_level": run.confidence_level,
        "contains_synthetic_data": run.contains_synthetic_data,
        "is_complete": run.is_complete,
    }


def run_full(run: ScenarioRun) -> dict[str, Any]:
    return {
        **run_brief(run),
        "profile": {
            "id": str(run.profile_id),
            "code": run.profile_code,
            "version": run.profile_version,
        },
        "versions": {
            "normalization": run.normalization_version,
            "engine": run.engine_version,
            "profile": run.profile_version,
        },
        "duration_ms": run.duration_ms,
        "traveler_count": run.traveler_count,
        "transport": {
            "p25": _money(run.transport_p25),
            "median": _money(run.transport_median),
            "p75": _money(run.transport_p75),
            "source_count": run.transport_source_count,
            "offer_count": run.transport_offer_count,
            "disagreement": run.transport_disagreement,
        },
        "accommodation": {
            "p25": _money(run.hotel_p25),
            "median": _money(run.hotel_median),
            "p75": _money(run.hotel_p75),
            "source_count": run.hotel_source_count,
            "offer_count": run.hotel_offer_count,
            "disagreement": run.hotel_disagreement,
        },
        "total": {
            "estimated_cost": _money(run.total_estimated_cost),
            "p25": _money(run.total_p25),
            "p75": _money(run.total_p75),
            "price_per_person": _money(run.price_per_person),
            "transport_share": run.transport_share,
        },
        "quality": {
            "score": run.quality_score,
            "breakdown": run.quality_breakdown,
        },
        "confidence": {
            "level": run.confidence_level,
            "reason": run.confidence_reason,
            "factors": run.confidence_factors,
        },
        "sources": {
            "count": run.source_count,
            "codes": list(run.source_codes or []),
        },
        "offers": {
            "valid": run.valid_offer_count,
            "excluded": run.excluded_offer_count,
            "outliers": run.outlier_offer_count,
        },
        "cache_key": run.cache_key,
        "served_from_cache": run.served_from_cache,
        "error_summary": list(run.error_summary or []),
        "job_id": str(run.job_id) if run.job_id else None,
        "created_by": run.created_by,
        "created_at": _iso(run.created_at),
    }


# --------------------------------------------------------------------------- #
# Предложения
# --------------------------------------------------------------------------- #


def offer(item: Offer) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "source_id": str(item.source_id),
        "source_code": item.source_code,
        "source_offer_id": item.source_offer_id,
        "offer_type": item.offer_type,
        "market_snapshot_id": str(item.market_snapshot_id) if item.market_snapshot_id else None,
        "collected_at": _iso(item.collected_at),
        "currency": item.currency,
        "total_price": _money(item.total_price),
        "price_per_night": _money(item.price_per_night),
        "validity_status": item.validity_status,
        "classification_status": item.classification_status,
        "exclusion_reason": item.exclusion_reason,
        "exclusion_detail": item.exclusion_detail,
        "technical_fingerprint": item.technical_fingerprint,
        "equivalence_group_id": str(item.equivalence_group_id)
        if item.equivalence_group_id
        else None,
        "is_duplicate": item.is_duplicate,
        "is_outlier": item.is_outlier,
        "matches_profile": item.matches_profile,
        "normalization_version": item.normalization_version,
        "raw_object_ref": item.raw_object_ref,
    }


# --------------------------------------------------------------------------- #
# Источники
# --------------------------------------------------------------------------- #


def source_brief(item: Source) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "category": item.category,
        "protocol": item.protocol,
        "offer_types": list(item.offer_types or []),
        "qualification_status": item.qualification_status,
        "is_enabled": item.is_enabled,
        "is_synthetic": item.is_synthetic,
        "is_usable": item.is_usable,
    }


def source_full(item: Source) -> dict[str, Any]:
    return {
        **source_brief(item),
        "qualification_notes": item.qualification_notes,
        "qualified_at": _iso(item.qualified_at),
        "requires_credentials": item.requires_credentials,
        "allowed_hosts": list(item.allowed_hosts or []),
        "legal_status": item.legal_status,
        "storage_allowed": item.storage_allowed,
        "html_storage_allowed": item.html_storage_allowed,
        "horizon": {
            "min_supported_date": _iso(item.min_supported_date),
            "max_supported_date": _iso(item.max_supported_date),
            "booking_horizon_days": item.booking_horizon_days,
            "checked_at": _iso(item.horizon_checked_at),
        },
        "supported_city_codes": list(item.supported_city_codes or []),
        "rate_limit_per_minute": item.rate_limit_per_minute,
        "connector_version": item.connector_version,
        "circuit": {
            "consecutive_failures": item.consecutive_failures,
            "open_until": _iso(item.circuit_open_until),
            "is_open": item.circuit_open_until is not None,
        },
        "last_success_at": _iso(item.last_success_at),
        "last_failure_at": _iso(item.last_failure_at),
        "last_error": item.last_error,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def source_metric(item: SourceMetric) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "source_id": str(item.source_id),
        "market_snapshot_id": str(item.market_snapshot_id) if item.market_snapshot_id else None,
        "offer_type": item.offer_type,
        "observed_at": _iso(item.observed_at),
        "outcome": item.outcome,
        "http_status": item.http_status,
        "latency_ms": item.latency_ms,
        "attempts": item.attempts,
        "raw_offer_count": item.raw_offer_count,
        "valid_offer_count": item.valid_offer_count,
        "invalid_offer_count": item.invalid_offer_count,
        "unclassified_offer_count": item.unclassified_offer_count,
        "required_field_completeness": item.required_field_completeness,
        "error_code": item.error_code,
        "error_message": item.error_message,
    }


def source_confidence(item: SourceConfidence) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "source_id": str(item.source_id),
        "source_code": item.source.code if item.source else None,
        "calculation_date": _iso(item.calculation_date),
        "formula_version": item.formula_version,
        "score": item.score,
        "effective_score": item.effective_score,
        "level": item.level,
        "input_metrics": item.input_metrics,
        "factor_scores": item.factor_scores,
        "manual_override": item.manual_override,
        "override_reason": item.override_reason,
        "approved_by": item.approved_by,
        "overridden_at": _iso(item.overridden_at),
        "created_at": _iso(item.created_at),
    }


# --------------------------------------------------------------------------- #
# Профили, экспорт, аудит
# --------------------------------------------------------------------------- #


def profile_brief(item: CalculationProfile) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "version": item.version,
        "version_seq": item.version_seq,
        "status": item.status,
        "label": item.label,
        "activated_at": _iso(item.activated_at),
        "archived_at": _iso(item.archived_at),
    }


def profile_full(item: CalculationProfile) -> dict[str, Any]:
    return {
        **profile_brief(item),
        "description": item.description,
        "rules": item.rules,
        "created_by": item.created_by,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def export_artifact(item: ExportArtifact) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "job_id": str(item.job_id),
        "dataset": item.dataset,
        "format": item.export_format,
        "filename": item.filename,
        "row_count": item.row_count,
        "size_bytes": item.size_bytes,
        "checksum_sha256": item.checksum_sha256,
        "filters": item.filters,
        "created_by": item.created_by,
        "created_at": _iso(item.created_at),
        "expires_at": _iso(item.expires_at),
    }


def audit_event(item: AuditEvent) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "action": item.action,
        "actor": {
            "user_id": item.actor_user_id,
            "username": item.actor_username,
            "role": item.actor_role,
        },
        "object_type": item.object_type,
        "object_id": item.object_id,
        "summary": item.summary,
        "payload": item.payload,
        "request_id": item.request_id,
        "ip_address": item.ip_address,
        "created_at": _iso(item.created_at),
    }
