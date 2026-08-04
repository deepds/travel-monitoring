# Карта Celery-задач

Все задачи объявлены через `@shared_task` и разрешаются в приложение
`tco.tasks.celery_app`, объявленное дефолтным через `set_default()`.

## Задачи

| Имя | Модуль | Очередь | Назначение |
|---|---|---|---|
| `tco.collect.collect_transport_offers` | `tasks/pipeline.py` | `collect` | сбор транспортных предложений в снимок |
| `tco.collect.collect_accommodation_offers` | `tasks/pipeline.py` | `collect` | сбор предложений проживания |
| `tco.snapshot.build_market_snapshot` | `tasks/pipeline.py` | `collect` | завершение снимка после сбора |
| `tco.calculate.calculate_scenario_run` | `tasks/pipeline.py` | `compute` | применение методики к снимку |
| `tco.calculate.replay_snapshot_with_profile` | `tasks/pipeline.py` | `compute` | пересчет снимка другим профилем |
| `tco.ondemand.run_on_demand_calculation` | `tasks/pipeline.py` | `ondemand` | полный расчет по запросу пользователя |
| `tco.monitoring.refresh_monitoring_scenario` | `tasks/pipeline.py` | `compute` | плановый снимок и расчет одного сценария |
| `tco.monitoring.refresh_all_monitoring_scenarios` | `tasks/pipeline.py` | `compute` | пакетный прогон всех активных сценариев |
| `tco.source.health_check_source` | `tasks/maintenance.py` | `maintenance` | проверка доступности источника |
| `tco.source.health_check_all_sources` | `tasks/maintenance.py` | `maintenance` | проверка всех источников |
| `tco.source.qualify_source` | `tasks/maintenance.py` | `maintenance` | квалификация источника |
| `tco.source.refresh_source_horizons` | `tasks/maintenance.py` | `maintenance` | обновление технического горизонта |
| `tco.metrics.calculate_source_confidence_all` | `tasks/maintenance.py` | `maintenance` | пересчет Source Confidence |
| `tco.metrics.calculate_quality_metrics` | `tasks/maintenance.py` | `maintenance` | агрегированные метрики качества |
| `tco.export.export_dataset` | `tasks/maintenance.py` | `maintenance` | выгрузка в CSV/XLSX |
| `tco.maintenance.import_scenarios_job` | `tasks/maintenance.py` | `maintenance` | фоновый импорт каталога |
| `tco.maintenance.cleanup_expired_raw_data` | `tasks/maintenance.py` | `maintenance` | очистка raw и HTML по retention |
| `tco.maintenance.cleanup_expired_cache` | `tasks/maintenance.py` | `maintenance` | очистка истекшего кэша |
| `tco.maintenance.cleanup_expired_data` | `tasks/maintenance.py` | `maintenance` | полный цикл retention |
| `tco.maintenance.detect_stalled_jobs` | `tasks/maintenance.py` | `maintenance` | перевод зависших задач в `TIMED_OUT` |
| `tco.maintenance.purge_result_cache` | `tasks/maintenance.py` | `maintenance` | полная очистка кэша |

## Расписание Beat

| Задача | Расписание | Зачем |
|---|---|---|
| `refresh_all_monitoring_scenarios` | `05 * * * *` | ежечасный снимок (24 в сутки) |
| `health_check_all_sources` | `20 * * * *` | ежечасно |
| `calculate_source_confidence_all` | `40 2 * * *` | ежедневно ночью |
| `refresh_source_horizons` | `50 3 * * *` | ежедневно |
| `cleanup_expired_data` | `15 4 * * *` | ежедневно |
| `cleanup_expired_cache` | `35 * * * *` | ежечасно |
| `detect_stalled_jobs` | `*/10 * * * *` | каждые 10 минут |

Смещение по минутам сделано намеренно: обслуживающие задачи не стартуют
одновременно с пакетом мониторинга.

## Оркестрация пакета мониторинга

```
refresh_all_monitoring_scenarios
    │  создает MONITORING_BATCH job (идемпотентно по 6-часовому окну)
    ▼
group(refresh_monitoring_scenario × N)          ← параллельно по сценариям
    │
    ├── create_snapshot        (идемпотентно по окну)
    ├── group(collect_* по источникам)          ← параллельно по источникам
    ├── chord callback → finalize_snapshot
    └── calculate_scenario_run
```

Параллелизм на уровне сценариев — узкое место при 100+ сценариях четыре раза
в сутки, поэтому масштабируется он именно там.

## Надежность

| Параметр | Значение | Настройка |
|---|---|---|
| Soft timeout вызова источника | 20 с | `CONNECTOR_SOFT_TIMEOUT_SECONDS` |
| Hard timeout вызова источника | 30 с | `CONNECTOR_HARD_TIMEOUT_SECONDS` |
| Общий timeout on-demand | 60 с | `ON_DEMAND_JOB_TIMEOUT_SECONDS` |
| Повторы | 2 | `CONNECTOR_MAX_RETRIES` |
| Backoff | экспоненциальный с джиттером | `CONNECTOR_BACKOFF_*` |
| Порог предохранителя | 5 подряд | `CONNECTOR_CIRCUIT_BREAKER_FAILURES` |
| Остывание предохранителя | 900 с | `CONNECTOR_CIRCUIT_BREAKER_COOLDOWN_SECONDS` |

Не повторяются ошибки `4xx`, кроме `408` и `429`.

Настройки Celery: `task_acks_late = true`,
`task_reject_on_worker_lost = true`, `worker_prefetch_multiplier = 1`,
`worker_max_tasks_per_child = 200`. Такая комбинация не теряет задачи при
падении воркера и не даёт одному воркеру набрать очередь себе в буфер.

## Диагностика

```bash
docker compose exec worker celery -A tco.tasks.celery_app:celery_app inspect active
docker compose exec worker celery -A tco.tasks.celery_app:celery_app inspect scheduled
docker compose exec worker celery -A tco.tasks.celery_app:celery_app inspect stats
```

Состояние задач доступно и через API: `GET /api/v1/jobs`,
`GET /api/v1/jobs/{id}/events`.
