# DELTA к SCOPE-R — Платформа мониторинга стоимости путешествий

## Назначение дополнения

Настоящий документ дополняет основной `SCOPE-R_Travel_Cost_Observatory_MVP.md` и фиксирует архитектурные решения, согласованные после его подготовки.

Документ не заменяет основной SCOPE-R и должен использоваться совместно с ним. При противоречиях положения настоящего дополнения имеют приоритет для следующих областей:

- Job Engine;
- API и версионирование;
- Market Snapshot;
- Offer Snapshot;
- хранение HTML;
- Source Confidence;
- Scenario Confidence;
- Calculation Methodology;
- Data Contract.

---

# 1. Изменение фундаментальной модели данных

## 1.1. Новая фундаментальная сущность — Market Snapshot

Главной первичной аналитической сущностью платформы считается не `ScenarioRun`, а `MarketSnapshot`.

`MarketSnapshot` фиксирует состояние рынка для конкретного туристического сценария и конкретного момента наблюдения до применения расчетной методики.

Логическая цепочка:

```text
TravelScenario
        ↓
MarketSnapshot
        ↓
Calculation Engine
        ↓
ScenarioRun
        ↓
Dashboard / Export / Analytics
```

## 1.2. Назначение Market Snapshot

`MarketSnapshot` должен позволять:

- сохранять набор рыночных предложений, использованных или потенциально пригодных для расчета;
- воспроизводить расчет без повторного обращения к внешнему API;
- пересчитывать один и тот же снимок разными версиями `CalculationProfile`;
- сравнивать разные методики на одном и том же наборе данных;
- проводить аудит результатов;
- исследовать влияние фильтрации, дедупликации и выбросов;
- сохранять доказательную базу при изменении или недоступности внешнего источника.

## 1.3. Минимальная структура MarketSnapshot

```text
MarketSnapshot
- id
- scenario_id
- snapshot_type
- requested_at
- completed_at
- observation_date
- source_ids
- transport_offer_count
- accommodation_offer_count
- valid_offer_count
- invalid_offer_count
- duplicate_offer_count
- outlier_offer_count
- raw_response_refs
- html_snapshot_refs
- normalization_version
- connector_versions
- status
- source_confidence_summary
- created_by
- created_at
```

Допустимые типы:

- `DAILY_MONITORING`;
- `ON_DEMAND`;
- `MANUAL_RETRY`;
- `METHODOLOGY_REPLAY`.

## 1.4. Связь MarketSnapshot и ScenarioRun

Один `MarketSnapshot` может иметь несколько `ScenarioRun`.

Пример:

```text
MarketSnapshot #125
    ├── ScenarioRun profile=v1.0
    ├── ScenarioRun profile=v1.1
    └── ScenarioRun experimental=P25
```

`ScenarioRun` обязан хранить ссылку на `market_snapshot_id`.

`ScenarioRun` остается неизменяемым историческим результатом конкретного расчета.

---

# 2. Политика снимков

## 2.1. Плановые Market Snapshot каждые 6 часов

Для каждого активного `Monitoring Scenario` создаются **четыре плановых `MarketSnapshot` в сутки** с интервалом **6 часов**.

Снимок создается:

- по расписанию каждые 6 часов (например, 00:00, 06:00, 12:00 и 18:00);
- после завершения сбора по обязательным источникам;
- даже если часть источников завершилась ошибкой;
- со статусом, отражающим полноту данных.

## 2.2. Offer Snapshot на момент запроса

Для каждого `ON_DEMAND` запроса создается отдельный `MarketSnapshot`, если подходящего свежего снимка нет в кэше.

Если результат возвращен из кэша:

- новый внешний сбор не выполняется;
- пользовательский расчет ссылается на существующий `MarketSnapshot`;
- факт использования кэша фиксируется в `ScenarioRun`.

## 2.3. Частота

Для MVP:

- Monitoring: четыре снимка в сутки (каждые 6 часов);
- On-demand: один снимок на новый некэшированный запрос;
- дополнительные внутридневные снимки допускаются при ручном запуске администратора;
- непрерывный мониторинг изменений предложений в MVP не требуется.

## 2.4. Retention

Рекомендуемая политика:

| Данные | Retention |
|---|---:|
| MarketSnapshot metadata | бессрочно |
| Offer Snapshot / normalized offers | 90 дней |
| Raw API responses | 30–90 дней |
| HTML snapshots | 30–90 дней |
| ScenarioRun | бессрочно |
| Aggregated source metrics | бессрочно |

Сроки конфигурируются.

---

# 3. Хранение HTML

## 3.1. Обязательное требование

Если источник обрабатывается через браузерный парсинг, Playwright или HTML-страницы, система должна сохранять HTML-снимок страницы, использованный при извлечении данных.

## 3.2. Формат

Рекомендуется:

- gzip-сжатие;
- хранение в S3/MinIO или файловом raw storage;
- имя объекта, содержащее source, scenario, timestamp и request id;
- вычисление checksum;
- ссылка из `MarketSnapshot` и `Offer`.

## 3.3. Дополнительные артефакты

При технической возможности сохранять:

- response headers;
- финальный URL;
- HTTP status;
- screenshot при ошибке парсинга;
- DOM extraction log;
- версию парсера.

## 3.4. Ограничение

HTML сохраняется только если это разрешено юридическими условиями источника и внутренней политикой хранения.

---

# 4. Job Engine на базе Celery

## 4.1. Решение

Для фоновых и долгих операций использовать Celery.

Рекомендуемая схема:

```text
FastAPI
   ↓
Celery Producer
   ↓
Redis / RabbitMQ broker
   ↓
Celery Workers
   ↓
PostgreSQL + Raw Storage
```

Для MVP предпочтителен Redis как broker и result backend, если он доступен.

## 4.2. Типы задач

Минимальный набор Celery tasks:

```text
qualify_source
collect_transport_offers
collect_accommodation_offers
persist_raw_response
persist_html_snapshot
normalize_offers
build_market_snapshot
calculate_scenario_run
calculate_quality_metrics
refresh_monitoring_scenario
refresh_all_monitoring_scenarios
replay_snapshot_with_profile
export_dataset
cleanup_expired_raw_data
cleanup_expired_cache
health_check_source
```

## 4.3. Оркестрация

Рекомендуемые Celery primitives:

- `group` — параллельный вызов нескольких источников;
- `chain` — последовательные этапы pipeline;
- `chord` — сбор результатов параллельных коннекторов с последующим построением `MarketSnapshot`;
- retry policy для временных ошибок;
- dead-letter / failed task registry через отдельную таблицу или мониторинг.

Пример:

```text
group(
  collect_tutu,
  collect_rzd,
  collect_yandex,
  collect_hotel_source
)
        ↓ chord callback
build_market_snapshot
        ↓
calculate_scenario_run
        ↓
update_cache
```

## 4.4. Статусы Job

```text
PENDING
QUEUED
RUNNING
PARTIAL
SUCCESS
FAILED
CANCELLED
RETRYING
TIMED_OUT
```

## 4.5. Идемпотентность

Каждая задача должна иметь idempotency key.

Для расчета:

```text
scenario fingerprint
+
requested_at bucket
+
profile version
+
run type
```

Повторный запуск не должен создавать дублирующий `MarketSnapshot`, если уже существует эквивалентный завершенный снимок и не запрошен `force_refresh`.

## 4.6. Timeouts и retry

Стартовые значения:

- soft timeout одного source call: 20 секунд;
- hard timeout одного source call: 30 секунд;
- общий timeout On-demand job: 60 секунд;
- retry: 2–3 попытки;
- exponential backoff;
- jitter;
- не повторять 4xx, кроме 408/429;
- circuit breaker реализовать через состояние источника и ограничение повторных запусков.

## 4.7. Celery Beat

Celery Beat используется для:

- ежедневного запуска Monitoring Scenario;
- периодического health check источников;
- очистки retention;
- обновления технического горизонта источников;
- контроля зависших jobs.

---

# 5. API платформы

## 5.1. Версионирование

Все API endpoint размещаются под префиксом:

```text
/api/v1
```

Версия должна присутствовать с первого релиза.

Breaking changes публикуются только в новой major API version.

## 5.2. Общие принципы

- JSON API;
- OpenAPI обязателен;
- Pydantic schemas;
- единый error envelope;
- pagination;
- filtering;
- sorting;
- request id;
- correlation id;
- idempotency key для создающих операций;
- роли проверяются на backend;
- длинные операции возвращают `202 Accepted` и `job_id`.

## 5.3. Error envelope

```json
{
  "error": {
    "code": "UNSUPPORTED_ROUTE",
    "message": "Маршрут не поддерживается",
    "details": {},
    "request_id": "uuid"
  }
}
```

---

# 6. Ключевые endpoint

## 6.1. Health и metadata

```text
GET /api/v1/health
GET /api/v1/health/ready
GET /api/v1/health/live
GET /api/v1/version
```

## 6.2. Справочники

```text
GET /api/v1/reference/cities
GET /api/v1/reference/transport-types
GET /api/v1/reference/accommodation-types
GET /api/v1/reference/meal-types
GET /api/v1/reference/fare-types
GET /api/v1/reference/rail-classes
GET /api/v1/reference/cancellation-types
```

## 6.3. Сценарии

```text
GET    /api/v1/scenarios
POST   /api/v1/scenarios
GET    /api/v1/scenarios/{scenario_id}
PATCH  /api/v1/scenarios/{scenario_id}
POST   /api/v1/scenarios/{scenario_id}/activate
POST   /api/v1/scenarios/{scenario_id}/deactivate
DELETE /api/v1/scenarios/{scenario_id}
```

Удаление рекомендуется реализовать как soft delete.

Фильтры:

- scenario type;
- origin;
- destination;
- active;
- date range;
- transport;
- accommodation type.

## 6.4. Импорт сценариев

```text
POST /api/v1/admin/scenarios/import
GET  /api/v1/admin/scenarios/import/{job_id}
GET  /api/v1/admin/scenarios/import/{job_id}/errors
```

Поддерживаемые форматы:

- CSV;
- YAML.

## 6.5. Templates

```text
GET  /api/v1/templates
GET  /api/v1/templates/{template_id}
POST /api/v1/templates/{template_id}/instantiate
```

## 6.6. On-demand расчет

```text
POST /api/v1/calculations
GET  /api/v1/calculations/{job_id}
POST /api/v1/calculations/{job_id}/cancel
```

Пример ответа на запуск:

```json
{
  "job_id": "uuid",
  "status": "QUEUED",
  "cached": false,
  "status_url": "/api/v1/calculations/uuid"
}
```

Если результат найден в кэше, endpoint может вернуть `200 OK` с готовым `ScenarioRun`.

## 6.7. Monitoring

```text
POST /api/v1/admin/monitoring/run
POST /api/v1/admin/monitoring/scenarios/{scenario_id}/run
GET  /api/v1/admin/monitoring/jobs
GET  /api/v1/admin/monitoring/jobs/{job_id}
```

## 6.8. Market Snapshots

```text
GET  /api/v1/market-snapshots
GET  /api/v1/market-snapshots/{snapshot_id}
GET  /api/v1/market-snapshots/{snapshot_id}/offers
GET  /api/v1/market-snapshots/{snapshot_id}/sources
GET  /api/v1/market-snapshots/{snapshot_id}/raw-artifacts
POST /api/v1/market-snapshots/{snapshot_id}/recalculate
```

`recalculate` создает новый `ScenarioRun` по выбранному профилю, не меняя исходный snapshot.

## 6.9. Scenario Runs

```text
GET /api/v1/scenario-runs
GET /api/v1/scenario-runs/{run_id}
GET /api/v1/scenario-runs/{run_id}/explain
GET /api/v1/scenario-runs/{run_id}/source-breakdown
```

Фильтры:

- scenario;
- snapshot;
- status;
- run type;
- observation date;
- profile version;
- quality threshold.

## 6.10. Dashboard

```text
GET /api/v1/dashboard/overview
GET /api/v1/dashboard/directions
GET /api/v1/dashboard/trends
GET /api/v1/dashboard/cost-structure
GET /api/v1/dashboard/changes
GET /api/v1/dashboard/quality
```

## 6.11. Источники

```text
GET   /api/v1/sources
GET   /api/v1/sources/{source_id}
PATCH /api/v1/sources/{source_id}
POST  /api/v1/sources/{source_id}/enable
POST  /api/v1/sources/{source_id}/disable
POST  /api/v1/sources/{source_id}/health-check
GET   /api/v1/sources/{source_id}/metrics
GET   /api/v1/sources/{source_id}/confidence
```

## 6.12. Calculation Profiles

```text
GET  /api/v1/calculation-profiles
POST /api/v1/calculation-profiles
GET  /api/v1/calculation-profiles/{profile_id}
POST /api/v1/calculation-profiles/{profile_id}/activate
POST /api/v1/calculation-profiles/{profile_id}/archive
POST /api/v1/calculation-profiles/{profile_id}/clone
```

ACTIVE profile immutable.

## 6.13. Jobs

```text
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/retry
POST /api/v1/jobs/{job_id}/cancel
GET  /api/v1/jobs/{job_id}/events
```

## 6.14. Export

```text
POST /api/v1/exports
GET  /api/v1/exports/{job_id}
GET  /api/v1/exports/{job_id}/download
```

Форматы:

- CSV;
- XLSX.

## 6.15. Audit

```text
GET /api/v1/audit/events
GET /api/v1/audit/events/{event_id}
```

---

# 7. Source Confidence

## 7.1. Назначение

`Source Confidence` отражает долгосрочную степень доверия к источнику как поставщику данных.

Это не то же самое, что технический health и не то же самое, что Quality Score конкретного расчета.

## 7.2. Факторы

Стартовая модель:

| Фактор | Вес |
|---|---:|
| Техническая стабильность за 30 дней | 20% |
| Полнота обязательных полей | 20% |
| Согласованность с другими источниками | 20% |
| Доля валидных предложений | 15% |
| Стабильность схемы | 10% |
| Юридическая и договорная надежность | 10% |
| Результаты ручной проверки | 5% |

## 7.3. Уровни

```text
HIGH       80–100
MEDIUM     60–79
LOW        40–59
UNTRUSTED   0–39
```

## 7.4. Использование

Для MVP Source Confidence:

- отображается в админском интерфейсе;
- влияет на Scenario Confidence;
- используется как сигнал допуска источника;
- не используется как непрозрачный вес цены;
- не заменяет source eligibility.

## 7.5. Версионирование

Формула и значение confidence должны иметь:

- calculation date;
- formula version;
- input metrics;
- manual override;
- override reason;
- approved by.

---

# 8. Scenario Confidence

## 8.1. Назначение

`Scenario Confidence` отражает степень уверенности в том, что итоговый ScenarioRun достаточно надежно представляет выбранный рыночный сценарий.

`Quality Score` оценивает техническое и статистическое качество конкретного расчета.

`Scenario Confidence` является интерпретируемым уровнем уверенности, учитывающим контекст.

## 8.2. Факторы

- Quality Score;
- число независимых источников;
- Source Confidence использованных источников;
- число валидных предложений;
- межисточниковое расхождение;
- доля неклассифицированных предложений;
- полнота компонентов;
- наличие single-source режима;
- свежесть снимка;
- прохождение challenge-set для похожего сценария.

## 8.3. Уровни

```text
HIGH
MEDIUM
LOW
INSUFFICIENT
```

Пример стартовой логики:

- `HIGH`: Quality >= 80, минимум 2 пригодных источника на компонент, низкое расхождение;
- `MEDIUM`: Quality 60–79 или один компонент основан на одном источнике;
- `LOW`: Quality 40–59, высокий разброс или слабые источники;
- `INSUFFICIENT`: отсутствует обязательный компонент или недостаточно данных.

## 8.4. Требования к UI

Рядом с итоговой стоимостью показывать:

```text
Расчетная типовая стоимость: 118 400 ₽
Диапазон: 105 000–136 000 ₽
Уверенность: MEDIUM
Причина: проживание рассчитано по одному источнику
```

## 8.5. Explainability

Scenario Confidence должен содержать список факторов, повысивших и снизивших уверенность.

---

# 9. Calculation Methodology

## 9.1. Обязательный отдельный документ

В составе проекта создать:

```text
docs/CALCULATION_METHODOLOGY.md
```

## 9.2. Содержание

Документ должен описывать:

1. определение расчетной типовой стоимости;
2. ограничения термина;
3. модель TravelScenario;
4. модель MarketSnapshot;
5. правила нормализации;
6. правила сопоставимости предложений;
7. тарифную классификацию;
8. вместимость;
9. питание;
10. отмену;
11. дедупликацию;
12. выбросы;
13. source eligibility;
14. per-source aggregation;
15. cross-source aggregation;
16. P25/median/P75;
17. расчет итоговой стоимости;
18. Quality Score;
19. Source Confidence;
20. Scenario Confidence;
21. partial results;
22. single-source режим;
23. rounding;
24. версионирование;
25. challenge-set;
26. известные ограничения.

## 9.3. Исследование альтернатив

Во время пилота допускается сравнение:

- median;
- P25;
- trimmed mean;
- winsorized mean;
- median-of-medians;
- weighted approaches.

Исторические ScenarioRun не переписываются автоматически.

Эксперименты выполняются как новые `ScenarioRun` на существующем `MarketSnapshot`.

---

# 10. Data Contract

## 10.1. Обязательный отдельный документ

Создать:

```text
docs/DATA_CONTRACT.md
```

## 10.2. Область

Data Contract должен описывать:

- `TravelScenario`;
- `ScenarioTemplate`;
- `CalculationProfile`;
- `RawResponse`;
- `HtmlSnapshot`;
- `Offer`;
- `FlightOffer`;
- `RailOffer`;
- `AccommodationOffer`;
- `MarketSnapshot`;
- `ScenarioRun`;
- `SourceMetric`;
- `SourceConfidence`;
- `ScenarioConfidence`;
- `Job`;
- `AuditEvent`.

## 10.3. Для каждого объекта

Обязательно указать:

- назначение;
- владелец;
- поле;
- тип;
- nullable;
- единицы;
- допустимые значения;
- источник;
- validation rule;
- default;
- PII classification;
- retention;
- versioning;
- backward compatibility;
- example payload.

## 10.4. Schema evolution

- schema version обязательна;
- коннектор не может передавать произвольную структуру напрямую в Calculation Engine;
- breaking schema changes требуют новой версии;
- unknown fields допустимы только в `source_payload` / raw layer;
- normalized model изменяется через migration и contract review.

---

# 11. Дополнительные критерии приемки

## 11.1. Market Snapshot

- создаются четыре snapshot в сутки (каждые 6 часов) на каждый активный Monitoring Scenario;
- snapshot immutable после завершения;
- содержит ссылки на raw и HTML;
- может использоваться повторно для пересчета;
- один snapshot поддерживает несколько ScenarioRun.

## 11.2. Celery

- долгие операции выполняются в background;
- API не блокируется на 30 секунд;
- job status доступен по API;
- retry не создает дубли;
- зависшая задача обнаруживается;
- Celery Beat запускает daily monitoring.

## 11.3. Source Confidence

- рассчитывается минимум раз в сутки;
- имеет версию формулы;
- доступен по API;
- объясним;
- ручной override аудируется.

## 11.4. Scenario Confidence

- рассчитывается для каждого ScenarioRun;
- отображается в UI;
- имеет объяснение;
- `INSUFFICIENT` запрещает показывать итог как полноценную стоимость.

## 11.5. HTML

- HTML сохраняется для браузерного источника;
- gzip;
- checksum;
- retention;
- ссылка из snapshot;
- токены и персональные данные не сохраняются.

---

# 12. Изменение deliverables

К ранее определенным deliverables добавить:

- `docs/CALCULATION_METHODOLOGY.md`;
- `docs/DATA_CONTRACT.md`;
- OpenAPI specification;
- Celery task map;
- Job lifecycle diagram;
- Market Snapshot schema;
- Source Confidence specification;
- Scenario Confidence specification;
- API examples;
- HTML retention policy;
- snapshot replay test;
- methodology comparison notebook или script.

---

# 13. Изменение архитектурной схемы

Обновленная логическая архитектура:

```text
Streamlit UI
     ↓
FastAPI /api/v1
     ↓
Job API
     ↓
Celery + Redis
     ↓
Connector Workers
     ↓
Raw API / HTML Storage
     ↓
Normalized Offers
     ↓
Market Snapshot
     ↓
Calculation Engine
     ↓
ScenarioRun
     ↓
Quality Score
Source Confidence
Scenario Confidence
     ↓
Dashboard / Export / Audit
```

---

# 14. Приоритет реализации

## P0

- Celery Job Engine;
- `/api/v1`;
- MarketSnapshot;
- Offer Snapshot;
- HTML storage;
- Calculation Methodology;
- Data Contract;
- Source Confidence;
- Scenario Confidence;
- snapshot replay.

## P1

- ручной override Source Confidence;
- расширенные API audit events;
- экспериментальные методики;
- UI сравнения нескольких профилей на одном snapshot.

---

# 15. Итоговое архитектурное решение

Платформа должна рассматриваться как система наблюдения за рынком, где:

- `TravelScenario` определяет объект наблюдения;
- `MarketSnapshot` фиксирует состояние рынка;
- `CalculationProfile` определяет методику;
- `ScenarioRun` фиксирует результат применения методики;
- `Source Confidence` оценивает доверие источникам;
- `Scenario Confidence` оценивает уверенность в результате;
- Celery обеспечивает асинхронное и воспроизводимое выполнение;
- API `/api/v1` является стабильным контрактом между backend, UI и внешними потребителями.


## Дополнение к разделу 2

### Обоснование четырех снимков в сутки

Для туристического рынка одного ежедневного наблюдения недостаточно, поскольку:

- стоимость авиабилетов может существенно изменяться в течение суток;
- гостиничные агрегаторы обновляют предложения несколько раз в день;
- часть источников синхронизируется с различной периодичностью;
- четырехкратное наблюдение позволяет лучше выявлять внутрисуточную волатильность без существенного роста стоимости эксплуатации.

В MVP четыре снимка в сутки считаются оптимальным компромиссом между стоимостью сбора данных и аналитической ценностью.

При этом в пользовательском дашборде по умолчанию отображается **последний актуальный Market Snapshot**, а исторический анализ может выполняться как по всем внутрисуточным снимкам, так и по агрегированному дневному представлению.
