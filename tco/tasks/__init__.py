"""Celery-задачи платформы. Полная карта — docs/CELERY_TASK_MAP.md."""

from tco.tasks.celery_app import celery_app
from tco.tasks.maintenance import (
    calculate_quality_metrics,
    calculate_source_confidence_all,
    cleanup_expired_cache,
    cleanup_expired_data,
    cleanup_expired_raw_data,
    detect_stalled_jobs,
    export_dataset,
    health_check_all_sources,
    health_check_source,
    import_scenarios_job,
    purge_result_cache,
    qualify_source,
    refresh_source_horizons,
)
from tco.tasks.pipeline import (
    build_market_snapshot,
    calculate_scenario_run,
    collect_accommodation_offers,
    collect_transport_offers,
    refresh_all_monitoring_scenarios,
    refresh_monitoring_scenario,
    replay_snapshot_with_profile,
    run_on_demand_calculation,
)

__all__ = [
    "build_market_snapshot",
    "calculate_quality_metrics",
    "calculate_scenario_run",
    "calculate_source_confidence_all",
    "celery_app",
    "cleanup_expired_cache",
    "cleanup_expired_data",
    "cleanup_expired_raw_data",
    "collect_accommodation_offers",
    "collect_transport_offers",
    "detect_stalled_jobs",
    "export_dataset",
    "health_check_all_sources",
    "health_check_source",
    "import_scenarios_job",
    "purge_result_cache",
    "qualify_source",
    "refresh_all_monitoring_scenarios",
    "refresh_monitoring_scenario",
    "refresh_source_horizons",
    "replay_snapshot_with_profile",
    "run_on_demand_calculation",
]
