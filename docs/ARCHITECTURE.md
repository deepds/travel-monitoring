# Архитектура

## Логическая схема

```mermaid
flowchart TD
    UI[React SPA<br/>nginx] --> API[FastAPI /api/v1]
    API --> JOB[Job API]
    JOB --> BROKER[(Redis<br/>broker + cache)]
    BROKER --> WORKER[Celery Workers]
    BEAT[Celery Beat] --> BROKER
    WORKER --> CONN[Connector Workers]
    CONN --> RAW[(Raw / HTML Storage)]
    CONN --> NORM[Normalized Offers]
    NORM --> SNAP[Market Snapshot]
    SNAP --> ENGINE[Calculation Engine]
    ENGINE --> RUN[ScenarioRun]
    RUN --> Q[Quality Score<br/>Source Confidence<br/>Scenario Confidence]
    Q --> OUT[Dashboard / Export / Audit]
    API --> DB[(PostgreSQL)]
    WORKER --> DB
    OUT --> API
```

## Слои кода

| Слой | Каталог | Ответственность | Зависит от |
|---|---|---|---|
| Ядро | `tco/core/` | конфигурация, логирование, ошибки, перечисления, RBAC | — |
| Данные | `tco/db/` | ORM-модели, сессии, кросс-диалектные типы | ядро |
| Контракты | `tco/schemas/` | Pydantic-схемы, правила профиля | ядро |
| Хранилища | `tco/storage/`, `tco/cache/` | raw-хранилище, кэш результатов | ядро, данные |
| Коннекторы | `tco/connectors/` | обращение к источникам, единый контракт | ядро |
| Нормализация | `tco/normalization/` | приведение к общей модели, классификация | ядро, данные |
| Движок | `tco/engine/` | отбор, агрегация, качество, уверенность | данные, контракты |
| Сервисы | `tco/services/` | сценарии использования | все нижние |
| Задачи | `tco/tasks/` | Celery-обвязка | сервисы |
| API | `tco/api/` | HTTP, RBAC, error envelope | сервисы, задачи |
| Интерфейс | `frontend/` | SPA | только API |

Зависимости направлены вниз. Движок не знает про HTTP и Celery, коннекторы не
знают про движок, интерфейс общается только через `/api/v1`.

## Поток расчета по запросу

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant A as API
    participant C as Result Cache
    participant W as Celery Worker
    participant S as Источники
    participant D as PostgreSQL

    U->>A: POST /calculations
    A->>A: валидация сценария
    Note over A: невалидный сценарий<br/>завершается без внешних запросов
    A->>C: чтение по ключу кэша
    alt Есть в кэше
        C-->>A: ScenarioRun
        A-->>U: 200 OK (cached)
    else Нет в кэше
        A->>D: создать Job (идемпотентно)
        A-->>U: 202 Accepted + job_id
        A->>W: поставить задачу
        W->>S: параллельный сбор по источникам
        S-->>W: ответы (или ошибки)
        W->>D: сохранить raw + нормализованные offers
        W->>D: собрать MarketSnapshot
        W->>W: применить методику
        W->>D: сохранить ScenarioRun
        W->>C: обновить кэш
        U->>A: GET /calculations/{job_id}
        A-->>U: статус + результат
    end
```

Ошибка одного источника не прерывает сбор остальных: снимок получает статус
`PARTIAL`, расчет выполняется на доступных данных, а причина фиксируется в
`SnapshotSourceResult` и в объяснимости.

## Жизненный цикл задачи

```mermaid
stateDiagram-v2
    [*] --> PENDING: создана
    PENDING --> QUEUED: поставлена в очередь
    QUEUED --> RUNNING: взята воркером
    RUNNING --> SUCCESS: выполнена
    RUNNING --> PARTIAL: частично
    RUNNING --> FAILED: ошибка
    RUNNING --> RETRYING: временная ошибка
    RETRYING --> RUNNING: повтор
    RETRYING --> FAILED: попытки исчерпаны
    RUNNING --> TIMED_OUT: нет heartbeat
    PENDING --> CANCELLED: отменена
    QUEUED --> CANCELLED: отменена
    RUNNING --> CANCELLED: отменена
    SUCCESS --> [*]
    PARTIAL --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
    TIMED_OUT --> [*]
```

Идемпотентность: ключ строится из отпечатка сценария, временного окна, версии
профиля и типа запуска. Повторный запуск в том же окне возвращает существующую
задачу вместо создания дубля.

Зависшая задача обнаруживается по отсутствию `heartbeat_at` дольше порога и
переводится в `TIMED_OUT` отдельной задачей Beat каждые 10 минут.

## Развертывание

```mermaid
flowchart LR
    subgraph host[Хост Docker]
        NGINX[ui<br/>nginx :80] -->|/api| API[api<br/>uvicorn :8000]
        API --> PG[(postgres :5432)]
        API --> RD[(redis :6379)]
        WRK[worker] --> PG
        WRK --> RD
        BT[beat] --> RD
        WRK --> VOL[/data/raw<br/>/data/exports/]
        API --> VOL
    end
    BROWSER[Браузер] -->|:8080| NGINX
```

Фронтенд и API находятся на одном origin: nginx проксирует `/api` на backend.
CORS не задействован, токен не пересекает границу источников.

Очереди Celery разделены по характеру нагрузки:

| Очередь | Задачи | Конкурирует за |
|---|---|---|
| `collect` | обращение к источникам | сеть |
| `compute` | расчет и мониторинг | CPU |
| `ondemand` | пользовательские запросы | приоритет отклика |
| `maintenance` | retention, метрики, экспорт | фон |

Разделение не даёт обслуживанию вытеснять пользовательские расчеты.

## Масштабирование

Текущая конфигурация рассчитана на одну VM и целевые 100+ сценариев мониторинга
четыре раза в сутки.

При росте нагрузки: увеличить число воркеров `collect`, вынести raw-хранилище
в S3/MinIO (`S3_ENDPOINT_URL`), добавить реплику PostgreSQL для чтения
аналитики. Ограничитель частоты API при горизонтальном масштабировании нужно
перенести в Redis — сейчас счетчик локален процессу (см.
[ограничения](LIMITATIONS.md)).
