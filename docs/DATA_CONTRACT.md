# Контракт данных

Документ обязателен по DELTA §10 и описывает нормализованную модель данных
платформы. Схема данных версионируется: `SCHEMA_VERSION = 1.0.0`.

Машиночитаемый контракт API — `docs/openapi.json` и `docs/openapi.yaml`.

## Общие правила

- **Владелец схемы** — команда платформы. Коннектор не может передавать
  произвольную структуру напрямую в Calculation Engine.
- **Неизвестные поля** допустимы только в raw-слое и в `source_payload` /
  `source_metadata`. В нормализованной модели их быть не может.
- **Ломающее изменение** требует новой `SCHEMA_VERSION` и миграции.
- **PII**: платформа не собирает персональные данные путешественников.
  Единственные персональные данные — учетные записи операторов
  (`users.username`, `email`). Токены и пароли в логи и raw-хранилище
  не попадают (`tco/core/logging.py:redact`).
- **Денежные величины**: `NUMERIC(14, 2)`, валюта — отдельное поле, по
  умолчанию `RUB`.
- **Время**: `TIMESTAMP WITH TIME ZONE`, хранится в UTC.
- **Идентификаторы**: UUID v4.

---

## TravelScenario

**Назначение.** Объект наблюдения: уникальная комбинация параметров
путешествия. **Retention** — бессрочно (мягкое удаление).

| Поле | Тип | Null | Ед. | Допустимые значения | Правило | Default |
|---|---|:--:|---|---|---|---|
| `id` | UUID | нет | | | | генерируется |
| `code` | str(96) | нет | | уникален | генерируется из параметров и отпечатка | — |
| `name` | str(255) | нет | | | человекочитаемое описание | генерируется |
| `scenario_type` | str(16) | нет | | `MONITORING`, `ON_DEMAND`, `TEMPLATE` | | `MONITORING` |
| `origin_city_id` | UUID | нет | | FK `cities` | ≠ destination | — |
| `destination_city_id` | UUID | нет | | FK `cities` | ≠ origin | — |
| `departure_date` | date | нет | | | ≥ сегодня при расчете | — |
| `return_date` | date | нет | | | > `departure_date` | — |
| `nights` | int | нет | ночей | ≥ 1 | денормализовано из дат | — |
| `adults` | int | нет | чел. | 1..9 | | 2 |
| `children_ages` | JSONB | нет | лет | массив 0..17 | до 8 элементов | `[]` |
| `transport_type` | str(8) | нет | | `AVIA`, `RAIL` | поддерживается городами | — |
| `flight_fare_type` | str(24) | да | | `CHEAPEST`, `CABIN_BAGGAGE`, `CHECKED_BAGGAGE` | обязателен при `AVIA` | `CHEAPEST` |
| `rail_class` | str(24) | да | | `RESERVED_SEAT`, `COMPARTMENT` | обязателен при `RAIL` | — |
| `accommodation_type` | str(24) | нет | | см. `AccommodationType` | `OTHER` только для хранения | `HOTEL` |
| `stars` | str(16) | нет | | `ANY`, `1`..`5`, `UNRATED`, `NOT_APPLICABLE` | применимо к `HOTEL`/`SANATORIUM` | `ANY` |
| `meal_type` | str(16) | нет | | см. `MealType` | в конструкторе — 3 значения | `ANY` |
| `cancellation_filter` | str(24) | нет | | `ANY`, `FREE_CANCELLATION` | | `ANY` |
| `calculation_profile_id` | UUID | да | | FK `calculation_profiles` | | активный профиль |
| `active_from` / `active_until` | date | да | | | автодеактивация после `active_until` | — |
| `is_active` | bool | нет | | | | `true` |
| `deleted_at` | timestamptz | да | | | мягкое удаление | `null` |
| `fingerprint` | str(64) | нет | | sha256 | стабилен для одинаковых параметров | вычисляется |
| `priority` | int | нет | | 0..1000 | порядок в пакете мониторинга | 100 |
| `tags` | JSONB | нет | | массив строк | | `[]` |

**Пример:**

```json
{
  "code": "MOW-AER-20260917-AVCHE-HOTE4-94A828",
  "name": "Москва → Сочи, 17.09.2026, 5 ноч., авиа, 2 взр.",
  "scenario_type": "MONITORING",
  "origin": {"code": "MOW", "name": "Москва"},
  "destination": {"code": "AER", "name": "Сочи"},
  "departure_date": "2026-09-17", "return_date": "2026-09-22", "nights": 5,
  "adults": 2, "children_ages": [], "traveler_count": 2,
  "transport_type": "AVIA", "flight_fare_type": "CHEAPEST",
  "accommodation_type": "HOTEL", "stars": "4",
  "meal_type": "ANY", "cancellation_filter": "ANY",
  "is_active": true
}
```

---

## ScenarioTemplate

**Назначение.** Частично заполненные параметры для конструктора.
**Retention** — бессрочно.

| Поле | Тип | Null | Описание |
|---|---|:--:|---|
| `code` | str(64) | нет | уникальный код шаблона |
| `name` | str(255) | нет | отображаемое имя |
| `description` | str(1024) | да | пояснение |
| `defaults` | JSONB | нет | частичный набор полей `TravelScenario` |
| `sort_order` | int | нет | порядок в списке |
| `is_active` | bool | нет | показывать ли в конструкторе |

---

## CalculationProfile

**Назначение.** Версионируемая методика расчета. **Retention** — бессрочно.
**ACTIVE-версия неизменяема.**

| Поле | Тип | Null | Допустимые значения | Правило |
|---|---|:--:|---|---|
| `code` | str(64) | нет | `[a-zA-Z0-9_-]+` | уникален с `version` |
| `version` | str(32) | нет | | уникальна в пределах `code` |
| `version_seq` | int | нет | ≥ 1 | монотонно растет |
| `status` | str(16) | нет | `DRAFT`, `ACTIVE`, `ARCHIVED` | одна ACTIVE на `code` |
| `rules` | JSONB | нет | схема `ProfileRules` | `extra="forbid"` |
| `activated_at` / `archived_at` | timestamptz | да | | проставляются переходом |

`rules` содержит секции: `filters`, `eligibility`, `outliers`, `aggregation`,
`quality`, `confidence`, `limits`, `transport_pricing`, а также
`allowed_source_codes`, `excluded_source_codes`, `rounding_digits`.
JSON Schema доступна через `GET /api/v1/calculation-profiles/{id}/rules-schema`.

---

## RawResponse

**Назначение.** Исходный ответ источника — доказательная база расчета.
**Retention** — `RETENTION_RAW_DAYS` (по умолчанию 45 дней).

| Поле | Тип | Null | Описание |
|---|---|:--:|---|
| `market_snapshot_id` | UUID | да | снимок, в рамках которого получен |
| `source_id` / `source_code` | UUID / str(64) | нет | источник |
| `offer_type` | str(24) | нет | `FLIGHT`, `RAIL`, `ACCOMMODATION` |
| `request_id` | str(64) | нет | корреляция с логами |
| `request_params` | JSONB | нет | параметры запроса **без секретов** |
| `endpoint` | str(512) | да | конечная точка |
| `http_status` | int | да | код ответа |
| `latency_ms` | int | да | задержка |
| `storage_ref` | str(1024) | нет | ссылка в raw-хранилище |
| `content_encoding` | str(32) | нет | `gzip` |
| `size_bytes` | bigint | нет | размер до сжатия |
| `checksum_sha256` | str(64) | нет | контрольная сумма |
| `expires_at` | timestamptz | да | срок хранения |
| `is_purged` | bool | нет | очищен ли retention |

Сохраняется только если `sources.storage_allowed = true`.

---

## HtmlSnapshot

**Назначение.** HTML-снимок страницы при браузерном парсинге (DELTA §3).
**Retention** — `RETENTION_HTML_DAYS`.

Поля дополнительно к `RawResponse`: `final_url`, `response_headers`,
`screenshot_ref`, `extraction_log`, `parser_version`.

Сохраняется только если `sources.html_storage_allowed = true`, то есть когда
это разрешено юридическими условиями источника. Токены и персональные данные
в снимок не попадают.

---

## Offer

**Назначение.** Нормализованное предложение. **Retention** —
`RETENTION_OFFERS_DAYS` (90 дней).

| Поле | Тип | Null | Ед. | Правило |
|---|---|:--:|---|---|
| `market_snapshot_id` | UUID | нет | | FK, каскадное удаление |
| `source_id` / `source_code` | UUID / str(64) | нет | | |
| `source_offer_id` | str(255) | да | | идентификатор у источника |
| `offer_type` | str(24) | нет | | `FLIGHT`, `RAIL`, `ACCOMMODATION` |
| `collected_at` | timestamptz | нет | | момент получения |
| `currency` | str(3) | нет | | ISO 4217; не `RUB` → отбраковка |
| `total_price` | numeric(14,2) | да | ₽ | транспорт — все пассажиры туда-обратно; проживание — номер за период |
| `price_per_night` | numeric(14,2) | да | ₽ | только диагностика |
| `validity_status` | str(24) | нет | | `VALID` или причина невалидности |
| `classification_status` | str(24) | нет | | `CLASSIFIED` или что не определено |
| `technical_fingerprint` | str(64) | нет | | для дедупликации внутри источника |
| `equivalence_key` | str(128) | да | | для связывания между источниками |
| `equivalence_group_id` | UUID | да | | группа эквивалентов |
| `is_duplicate` | bool | нет | | технический дубликат |
| `is_outlier` | bool | нет | | размечен по IQR |
| `matches_profile` | bool | нет | | прошел фильтр профиля |
| `exclusion_reason` | str(32) | нет | | `NONE` = учтено в расчете |
| `exclusion_detail` | str(512) | да | | человекочитаемая причина |
| `raw_object_ref` | str(1024) | да | | ссылка на исходный ответ |
| `normalization_version` | str(32) | нет | | версия маппинга |
| `deeplink` | str(2048) | да | | ссылка на предложение у источника |
| `detail` | object | да | | предметная часть, см. ниже |

**Специализированные таблицы** (`FlightOffer`, `RailOffer`,
`AccommodationOffer`) хранят предметные поля: сегменты и багаж для авиа,
станции и тип вагона для ЖД, объект размещения, вместимость, питание и
условие отмены для проживания.

В API они отдаются плоским объектом в поле `detail`; какой из трех наборов
полей пришел, определяется значением `offer_type`. Сырые JSONB-массивы
(`segments`, `outbound_segments`, `inbound_segments`, `amenities`) в `detail`
не входят — они неограниченного размера и не используются интерфейсом.

---

## MarketSnapshot

**Назначение.** Состояние рынка до применения методики. **Неизменяем** после
завершения. **Retention** — метаданные бессрочно, предложения по
`RETENTION_OFFERS_DAYS`.

| Поле | Тип | Null | Описание |
|---|---|:--:|---|
| `scenario_id` | UUID | нет | наблюдаемый сценарий |
| `scenario_fingerprint` | str(64) | нет | отпечаток на момент снимка |
| `snapshot_type` | str(24) | нет | `DAILY_MONITORING`, `ON_DEMAND`, `MANUAL_RETRY`, `METHODOLOGY_REPLAY` |
| `requested_at` / `completed_at` | timestamptz | нет / да | границы сбора |
| `observed_at` | timestamptz | нет | момент наблюдения рынка |
| `observation_date` | date | нет | дата наблюдения |
| `observation_slot` | int | да | внутрисуточное окно 0..3 |
| `status` | str(16) | нет | `COLLECTING`, `COMPLETE`, `PARTIAL`, `EMPTY`, `FAILED` |
| `source_ids` / `source_codes` | JSONB | нет | участвовавшие источники |
| `transport_offer_count` | int | нет | предложений транспорта |
| `accommodation_offer_count` | int | нет | предложений проживания |
| `valid_offer_count` | int | нет | валидных |
| `invalid_offer_count` | int | нет | невалидных |
| `duplicate_offer_count` | int | нет | дубликатов |
| `outlier_offer_count` | int | нет | выбросов |
| `raw_response_refs` | JSONB | нет | ссылки на raw |
| `html_snapshot_refs` | JSONB | нет | ссылки на HTML |
| `normalization_version` | str(32) | нет | версия нормализации |
| `connector_versions` | JSONB | нет | версии коннекторов |
| `source_confidence_summary` | JSONB | нет | доверие на момент снимка |
| `collection_summary` | JSONB | нет | технические итоги по источникам |
| `scenario_params` | JSONB | нет | параметры сценария на момент снимка |
| `contains_synthetic_data` | bool | нет | участвовала ли песочница |
| `idempotency_key` | str(64) | нет | уникален; окно + отпечаток + тип |
| `offers_purged_at` | timestamptz | да | предложения очищены retention |

**Обратная совместимость.** Добавление полей допустимо; удаление и изменение
семантики требуют новой `SCHEMA_VERSION`.

---

## SnapshotSourceResult

**Назначение.** Итог обращения к одному источнику в рамках снимка.
**Retention** — как метаданные снимка (бессрочно).

Дублирует часть `SourceMetric` намеренно: объяснимость расчета должна
сохраняться и после очистки предложений и метрик по retention.

Ключ: (`market_snapshot_id`, `source_id`, `offer_type`). Поля: `outcome`,
`latency_ms`, `attempts`, счетчики предложений,
`required_field_completeness`, `error_code`, `error_message`,
`connector_version`.

---

## ScenarioRun

**Назначение.** Неизменяемый исторический результат применения методики.
**Retention** — бессрочно.

| Группа | Поля |
|---|---|
| Связи | `scenario_id`, `market_snapshot_id`, `profile_id`, `job_id` |
| Время | `started_at`, `completed_at`, `duration_ms`, `observation_date`, `lead_time_days` |
| Версии | `profile_code`, `profile_version`, `normalization_version`, `engine_version` |
| Статус | `status`, `component_statuses` |
| Транспорт | `transport_p25`, `transport_median`, `transport_p75`, `transport_source_count`, `transport_offer_count`, `transport_disagreement` |
| Проживание | `hotel_p25`, `hotel_median`, `hotel_p75`, `hotel_source_count`, `hotel_offer_count`, `hotel_disagreement` |
| Итог | `total_estimated_cost`, `total_p25`, `total_p75`, `price_per_person`, `transport_share`, `currency`, `traveler_count` |
| Качество | `quality_score`, `quality_breakdown` |
| Уверенность | `confidence_level`, `confidence_reason`, `confidence_factors` |
| Источники | `source_count`, `source_codes` |
| Предложения | `valid_offer_count`, `excluded_offer_count`, `outlier_offer_count` |
| Объяснимость | `explainability_payload`, `source_breakdown` |
| Кэш | `cache_key`, `served_from_cache` |
| Прочее | `contains_synthetic_data`, `error_summary`, `created_by`, `created_at` |

`total_estimated_cost = null` означает, что компонент отсутствует. Это **не**
ошибка данных, а честное состояние: старое значение не подставляется.

---

## SourceMetric

**Назначение.** Технические метрики одного обращения к источнику.
**Retention** — 90 дней (агрегаты — бессрочно).

Поля: `source_id`, `market_snapshot_id`, `scenario_id`, `offer_type`,
`observed_at`, `outcome`, `http_status`, `latency_ms`, `attempts`, счетчики
предложений, `required_field_completeness`, `error_code`, `error_message`,
`connector_version`.

`outcome`: `SUCCESS`, `EMPTY`, `TIMEOUT`, `RATE_LIMITED`, `AUTH_ERROR`,
`SCHEMA_ERROR`, `TRANSPORT_ERROR`, `UNSUPPORTED`, `DISABLED`, `CIRCUIT_OPEN`.

---

## SourceConfidence

**Назначение.** Долгосрочное доверие к источнику. **Retention** — бессрочно.
Ключ: (`source_id`, `calculation_date`).

| Поле | Тип | Null | Описание |
|---|---|:--:|---|
| `score` | float | нет | 0..100, расчетное значение |
| `level` | str(16) | нет | `HIGH`, `MEDIUM`, `LOW`, `UNTRUSTED` |
| `formula_version` | str(32) | нет | версия формулы |
| `input_metrics` | JSONB | нет | входные значения факторов |
| `factor_scores` | JSONB | нет | вклад каждого фактора |
| `manual_override` | float | да | ручное значение 0..100 |
| `override_reason` | str(1024) | да | **обязательна** при override |
| `approved_by` | str(64) | да | кто задал |
| `overridden_at` | timestamptz | да | когда |

`effective_score = manual_override ?? score` — исходный расчет всегда
сохраняется рядом.

---

## Source

**Назначение.** Подключенный источник данных. **Retention** — бессрочно.

Ключевые поля: `code`, `name`, `category` (`TRANSPORT` / `ACCOMMODATION`),
`protocol` (`REST` / `MCP` / `HTML` / `SYNTHETIC`), `offer_types`,
`qualification_status` (`APPROVED` / `CONDITIONAL` / `REJECTED` /
`CANDIDATE`), `is_enabled`, `is_synthetic`, `allowed_hosts`, `legal_status`,
`storage_allowed`, `html_storage_allowed`, горизонт
(`min_supported_date`, `max_supported_date`, `booking_horizon_days`),
`rate_limit_per_minute`, `connector_version`, состояние предохранителя
(`consecutive_failures`, `circuit_open_until`).

**Учетные данные в этой таблице не хранятся** — только в окружении.
`allowed_hosts` служит allowlist-проверкой: коннектор не может обратиться к
произвольному URL.

---

## Job и JobEvent

**Назначение.** Фоновая задача и хронология ее состояний.
**Retention** — бессрочно (события — по мере роста).

| Поле | Тип | Описание |
|---|---|---|
| `job_type` | str(32) | см. `JobType` |
| `status` | str(16) | `PENDING`, `QUEUED`, `RUNNING`, `PARTIAL`, `SUCCESS`, `FAILED`, `CANCELLED`, `RETRYING`, `TIMED_OUT` |
| `idempotency_key` | str(96) | **уникален**; отпечаток + окно + версия профиля + тип запуска |
| `parent_job_id` | UUID | для задач пакета мониторинга |
| `params` / `result` | JSONB | вход и итог |
| `progress_*` | int / str | прогресс выполнения |
| `attempts` / `max_attempts` | int | попытки |
| `heartbeat_at` | timestamptz | признак живости для детектора зависших |
| `request_id` / `correlation_id` | str(64) | сквозная трассировка |

---

## ExportArtifact

**Назначение.** Результат выгрузки. **Retention** — `RETENTION_EXPORT_DAYS`
(7 дней).

Поля: `job_id`, `dataset`, `export_format`, `filename`, `storage_ref`,
`row_count`, `size_bytes`, `checksum_sha256`, `filters`, `expires_at`.

---

## AuditEvent

**Назначение.** Аудит административных и значимых действий.
**Retention** — бессрочно.

Поля: `action` (см. `AuditAction`), `actor_user_id`, `actor_username`,
`actor_role`, `object_type`, `object_id`, `summary`, `payload`, `request_id`,
`ip_address`, `user_agent`, `created_at`.

`payload` проходит через `redact()`: секреты в аудит не попадают.

---

## User

**Назначение.** Локальная учетная запись оператора (режим `LOCAL`).
**PII**: `username`, `email`, `display_name`. **Retention** — до удаления
учетной записи.

Пароль хранится только как bcrypt-хеш (`password_hash`, cost 12). В режиме
`OIDC` таблица не используется — субъект берется из внешнего токена.

---

## ResultCacheEntry

**Назначение.** PostgreSQL-backed кэш результатов. **Retention** — по
`expires_at`.

Ключ кэша учитывает все бизнес-параметры сценария и версию профиля.
PostgreSQL — источник истины, Redis — быстрый слой: in-memory кэш недопустим
как единственное решение при нескольких процессах.

---

## Эволюция схемы

1. `SCHEMA_VERSION` обязательна и хранится в каждом `ScenarioRun`.
2. Коннектор не передает произвольную структуру в Calculation Engine —
   только нормализованную модель.
3. Ломающие изменения требуют новой major-версии схемы.
4. Неизвестные поля допустимы только в raw-слое.
5. Нормализованная модель меняется через Alembic-миграцию и пересмотр этого
   документа.
