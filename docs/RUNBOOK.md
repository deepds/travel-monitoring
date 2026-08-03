# Runbook

Эксплуатационное руководство: развертывание, регламентные операции,
диагностика, резервное копирование и восстановление.

Адресат — дежурный инженер и администратор платформы.

---

## 1. Состав стека

| Сервис | Роль | Порт | Критичность |
|---|---|---:|---|
| `postgres` | Основное хранилище: сценарии, снимки, предложения, результаты | 5432 | Критичен |
| `redis` | Брокер Celery, result backend, быстрый слой Result Cache | 6379 | Деградация |
| `api` | FastAPI `/api/v1` | 8000 | Критичен |
| `worker` | Celery-воркер: сбор, расчет, обслуживание | — | Критичен |
| `beat` | Celery Beat: расписание снимков и регламентов | — | Критичен |
| `ui` | React-интерфейс за nginx | 8080 | Деградация |
| `migrate` | Разовое применение миграций (профиль `tools`) | — | — |
| `bootstrap` | Разовая инициализация справочников (профиль `tools`) | — | — |

Очереди Celery разделены намеренно: `collect` (сеть), `compute` (расчет),
`ondemand` (пользовательские запросы), `maintenance` (регламенты). Обслуживание
не должно вытеснять пользовательские расчеты.

---

## 2. Первичное развертывание

```bash
git clone <repo> && cd travel_monitoring
cp .env.example .env
```

Заполните обязательные секреты — без них стек не поднимется в проде:

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> .env
echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env
echo "BOOTSTRAP_ADMIN_PASSWORD=$(openssl rand -hex 12)" >> .env
```

Примените миграции и инициализируйте справочники:

```bash
docker compose --profile tools run --rm bootstrap
```

Команда дожидается готовности PostgreSQL, применяет Alembic-миграции и
заполняет города, профили расчета, источники, шаблоны и учетные записи.
Если пароли пользователей не заданы в окружении, они генерируются и
однократно печатаются в вывод — сохраните их сразу, в БД лежит только хеш.

Поднимите сервисы:

```bash
docker compose up -d
docker compose ps
```

Загрузите каталог сценариев мониторинга:

```bash
docker compose exec api python -m tco.cli import-scenarios catalog/monitoring_scenarios.csv
```

Проверьте готовность:

```bash
curl -fsS http://localhost:8000/api/v1/health/ready
curl -fsS http://localhost:8000/api/v1/version
```

---

## 3. Регламентные операции

### 3.1. Расписание Celery Beat

| Задача | Расписание | Назначение |
|---|---|---|
| `refresh_all_monitoring_scenarios` | каждые 6 часов, :05 | Четыре плановых снимка в сутки |
| `health_check_all_sources` | ежечасно, :20 | Доступность источников |
| `calculate_source_confidence_all` | ежедневно, 02:40 | Пересчет доверия источникам |
| `refresh_source_horizons` | ежедневно, 03:50 | Обновление технического горизонта |
| `cleanup_expired_data` | ежедневно, 04:15 | Retention: raw, HTML, offers, экспорты |
| `cleanup_expired_cache` | ежечасно, :35 | Очистка истекшего кэша |
| `detect_stalled_jobs` | каждые 10 минут | Обнаружение зависших задач |

Проверить, что Beat жив:

```bash
docker compose logs --tail=50 beat
docker compose exec api python -m tco.cli health
```

### 3.2. Ручной прогон мониторинга

```bash
# Через API (асинхронно, вернет job_id)
curl -X POST http://localhost:8000/api/v1/admin/monitoring/run \
  -H "Authorization: Bearer $TOKEN"

# Синхронно, минуя брокер — для отладки
docker compose exec api python -m tco.cli run-monitoring --limit 5
```

### 3.3. Пересчет Source Confidence

```bash
docker compose exec api python -m tco.cli source-confidence
```

### 3.4. Применение retention

```bash
docker compose exec api python -m tco.cli retention
```

Политика по умолчанию: raw и HTML — 45 дней, нормализованные предложения —
90 дней, метаданные снимков и `ScenarioRun` — бессрочно. Сроки настраиваются
переменными `RETENTION_*`.

---

## 4. Диагностика

### 4.1. Быстрая проверка состояния

```bash
docker compose ps
curl -s http://localhost:8000/api/v1/health | python -m json.tool
docker compose exec api python -m tco.cli health
```

`/health/ready` проверяет БД, брокер и raw storage; `/health/live` отвечает,
пока процесс жив. Оркестратор должен использовать `ready` для трафика и
`live` для перезапуска.

### 4.2. Типовые инциденты

#### Расчеты идут, но все сценарии в статусе `NO_DATA`

Вероятная причина — источники выключены или не прошли квалификацию.

```bash
curl -s http://localhost:8000/api/v1/sources -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Проверьте `is_enabled`, `qualification_status` и `circuit_open_until`.
Разомкнутый предохранитель закрывается автоматически по истечении
`CONNECTOR_CIRCUIT_BREAKER_COOLDOWN_SECONDS`, принудительно — через
`POST /api/v1/sources/{id}/enable`.

#### Источник помечен как недоступный

```bash
curl -X POST http://localhost:8000/api/v1/sources/{id}/health-check \
  -H "Authorization: Bearer $TOKEN"
```

Разберите `last_error` и `error_breakdown` на экране качества источников.
`AUTH_ERROR` означает истекшие или отсутствующие учетные данные —
проверьте `YANDEX_TRAVEL_TOKEN` / `TRAVELLINE_CLIENT_*` в окружении.

#### Задачи копятся в очереди

```bash
docker compose exec redis redis-cli llen collect
docker compose exec redis redis-cli llen compute
docker compose logs --tail=100 worker
```

Масштабирование воркеров:

```bash
docker compose up -d --scale worker=3
```

#### Зависшие задачи

Детектор переводит задачи без heartbeat дольше 15 минут в `TIMED_OUT`
каждые 10 минут. Список:

```bash
curl -s "http://localhost:8000/api/v1/jobs?status=TIMED_OUT" \
  -H "Authorization: Bearer $TOKEN"
```

Повторный запуск: `POST /api/v1/jobs/{id}/retry`. Ретрай не создает
дубликат снимка — идемпотентность обеспечивается ключом задачи.

#### Redis недоступен

Result Cache деградирует на слой PostgreSQL и продолжает работать.
Celery без брокера ставить задачи не может: API вернет `503`
(`BROKER_UNAVAILABLE`), синхронные расчеты через CLI останутся доступны.

#### PostgreSQL недоступен

Полная остановка обслуживания. Проверьте том `pgdata` и логи:

```bash
docker compose logs --tail=100 postgres
docker volume inspect travel_monitoring_pgdata
```

#### Raw storage недоступен

Сбор продолжается: ошибка фиксируется в метриках источника и в
`error_summary` снимка, но расчет не срывается. Нормализованные предложения
сохраняются в БД, теряется только возможность аудита исходных ответов.

### 4.3. Диагностика конкретного расчета

```bash
curl -s http://localhost:8000/api/v1/scenario-runs/{run_id}/explain \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Ответ содержит: использованные источники, число полученных и исключенных
предложений, медиану каждого источника, межисточниковое расхождение,
примененный профиль и версии, причины снижения Quality Score.

Разрез по источникам — `/scenario-runs/{run_id}/source-breakdown`.

---

## 5. Резервное копирование

### 5.1. Что резервируется

| Данные | Способ | Периодичность | Критичность |
|---|---|---|---|
| PostgreSQL | `pg_dump` | Ежедневно | Критично: восстановлению не подлежит из других источников |
| Raw storage | Синхронизация каталога / S3 | Ежедневно | Важно: доказательная база аудита |
| `.env` | Хранилище секретов организации | При изменении | Критично |

`ScenarioRun` и метаданные снимков хранятся бессрочно и невосстановимы:
внешние источники не отдают исторические цены.

### 5.2. Резервная копия

```bash
docker compose exec -T postgres pg_dump -U tco -Fc tco > backup-$(date +%F).dump
tar czf raw-$(date +%F).tar.gz -C var raw
```

### 5.3. Восстановление

```bash
docker compose stop api worker beat
docker compose exec -T postgres pg_restore -U tco -d tco --clean --if-exists < backup-2026-08-04.dump
tar xzf raw-2026-08-04.tar.gz -C var
docker compose start api worker beat
curl -fsS http://localhost:8000/api/v1/health/ready
```

Восстановление обязано проверяться регулярно: невоспроизведенная резервная
копия резервной копией не является.

---

## 6. Обновление версии

```bash
git pull
docker compose build
docker compose --profile tools run --rm migrate
docker compose up -d
```

Миграции применяются до подъема новых контейнеров. При изменении
`ENGINE_VERSION` или `NORMALIZATION_VERSION` исторические `ScenarioRun`
**не пересчитываются**: они остаются с версиями, при которых были получены.
Сравнивать результаты разных версий следует через пересчет снимка
(`POST /api/v1/market-snapshots/{id}/recalculate`), а не подменой истории.

---

## 7. Мониторинг

Минимальный набор наблюдаемых метрик (SCOPE-R R §4):

| Метрика | Источник | Порог внимания |
|---|---|---|
| Connector success rate | `/api/v1/sources/{id}/metrics` | < 90% за сутки |
| Latency источника | там же | p95 > 20 с |
| Доля валидных предложений | там же | < 70% |
| Scenario success rate | `/api/v1/dashboard/quality` | < 80% |
| Доля частичных результатов | там же | рост неделя к неделе |
| Cache hit rate | `/api/v1/health` | резкое падение |
| Длительность batch | `/api/v1/admin/monitoring/jobs` | > 2 часов |
| Рост БД и raw storage | `df -h`, `pg_database_size` | > 80% диска |

KPI стабильности MVP: не менее 80% ежедневных сценарных задач завершаются
`SUCCESS` либо `PARTIAL_SUCCESS` с Quality Score выше порога. Доли `SUCCESS`
и `PARTIAL_SUCCESS` показываются раздельно.

---

## 8. Безопасность в эксплуатации

- Секреты только в окружении; `.env` не коммитится и не копируется в образ.
- Логи и аудит проходят вычистку: токены и пароли в них не попадают.
- Порт PostgreSQL по умолчанию слушает `127.0.0.1` — наружу не публикуется.
- Смена пароля: `python -m tco.cli reset-password <username>`.
- Режим `DEPLOYMENT_MODE=OPEN` отключает авторизацию. Допустим только как
  временный режим развертывания, отражается в `/api/v1/version` и в интерфейсе.
- Административные действия пишутся в аудит: `/api/v1/audit/events`.
