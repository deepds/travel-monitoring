# Travel Cost Observatory

Платформа мониторинга стоимости путешествий: регулярно собирает рыночные
предложения по транспорту и проживанию, приводит их к общей модели,
воспроизводимо рассчитывает **расчетную типовую стоимость путешествия**,
накапливает исторические снимки рынка и показывает динамику.

> Показатель является расчетной оценкой на основе доступных рыночных
> предложений. Это **не оферта, не средний чек и не фактическая стоимость
> поездки**. Расходы на развлечения, питание вне объекта размещения,
> трансферы и локальные траты не учитываются.

## Что внутри

```
TravelScenario  →  MarketSnapshot  →  Calculation Engine  →  ScenarioRun
   объект            состояние            методика              результат
 наблюдения           рынка              (профиль)          (неизменяемый)
```

`MarketSnapshot` — первичная аналитическая сущность. Один снимок обслуживает
несколько расчетов с разными профилями, поэтому методики можно сравнивать на
одних и тех же данных, а результат — воспроизвести без повторного обращения
к внешним источникам.

## Возможности

- Ежечасный плановый снимок рынка по каждому активному сценарию.
- Расчет пользовательского сценария по запросу с кэшем результатов.
- Изолированные коннекторы: таймауты, ретраи, предохранитель, allowlist хостов.
- Сохранение исходных ответов и HTML с контрольной суммой и сроком хранения.
- Дедупликация, разметка выбросов по IQR, допуск источников, агрегация
  P25 / медиана / P75.
- Quality Score, Source Confidence и Scenario Confidence — с объяснением.
- Дашборд, конструктор сценария, анализ направления, экран качества
  источников, администрирование и аудит.
- Экспорт в CSV и XLSX с полями качества и версией методики.
- Роли `VIEWER` / `ANALYST` / `ADMIN`, проверяемые на backend.

## Технологии

| Слой | Стек |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2 |
| Хранилище | PostgreSQL 16, Redis, файловое / S3-совместимое raw-хранилище |
| Фоновые задачи | Celery + Celery Beat |
| Интерфейс | React 18, TypeScript, Vite, Ant Design, ECharts |
| Развертывание | Docker Compose, nginx |

## Быстрый старт

```bash
cp .env.example .env
```

Заполните обязательные значения:

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 16)
JWT_SECRET=$(openssl rand -hex 32)
```

Разверните стек:

```bash
docker compose --profile tools run --rm bootstrap
```

Эта команда дождется базы, применит миграции и заполнит справочники, профили,
источники и учетные записи. Затем поднимите сервисы:

```bash
docker compose up -d
```

Доступ:

| Что | Адрес |
|---|---|
| Интерфейс | http://localhost:8080 |
| API | http://localhost:8000/api/v1 |
| Swagger UI | http://localhost:8000/api/v1/docs |
| ReDoc | http://localhost:8000/api/v1/redoc |
| OpenAPI | http://localhost:8000/api/v1/openapi.json |

## Развертывание на новой машине (Ubuntu 24.04)

Проверено на чистой виртуальной машине. Все команды — от пользователя с
`sudo`. Минимум: 4 ГБ ОЗУ, 2 ядра, 40 ГБ диска (raw-хранилище растет быстрее
всего остального).

### 1. Docker и Compose v2

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
```

```bash
sudo install -m 0755 -d /etc/apt/keyrings && sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Проверка:

```bash
sudo docker compose version
```

Чтобы не писать `sudo` перед каждой командой (потребуется перелогиниться):

```bash
sudo usermod -aG docker $USER
```

### 2. Каталог данных вне репозитория

Накопленные наблюдения невосстановимы — источники не отдают историю цен.
Поэтому база, сырые ответы и выгрузки лежат отдельно от исходников и
переживают пересоздание репозитория.

```bash
sudo mkdir -p /srv/tco/{pgdata,raw,exports} /srv/tco-backups && sudo chown -R $USER:$USER /srv/tco /srv/tco-backups
```

### 3. Исходники из GitHub

```bash
git clone https://github.com/deepds/travel-monitoring.git ~/tco && cd ~/tco
```

### 4. Конфигурация

```bash
cp .env.example .env
```

Обязательные значения — сгенерируйте и впишите в `.env`:

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"; echo "JWT_SECRET=$(openssl rand -hex 32)"
```

Что еще стоит проверить в `.env` перед первым запуском:

| Переменная | Зачем |
|---|---|
| `DATA_ROOT=/srv/tco` | каталог данных из шага 2 |
| `ENVIRONMENT` | `staging` или `prod` — влияет на метки в интерфейсе |
| `DEPLOYMENT_MODE=LOCAL` | `OPEN` отключает авторизацию, только временно |
| `UI_PORT` / `API_PORT` | порты наружу, по умолчанию 8080 и 8000 |
| `POSTGRES_PORT` | оставьте `127.0.0.1:5432`, чтобы база не смотрела в сеть |
| `SANDBOX_SOURCES_ENABLED=false` | в рабочем развертывании обязательно |
| `SNAPSHOT_INTERVAL_HOURS` | частота наблюдения; увеличение кратно грузит источники |

Пароли учетных записей лучше задать здесь же. Если оставить их пустыми, они
сгенерируются, но попадут только в вывод команды `bootstrap` — в логах
контейнеров их нет, и потерянный пароль придется сбрасывать через
`python -m tco.cli reset-password`.

### 5. Инициализация и запуск

```bash
sudo docker compose --profile tools run --rm bootstrap
```

Команда дождется базы, применит миграции и заполнит справочники, профили,
источники и учетные записи. Затем:

```bash
sudo docker compose up -d
```

### 6. Данные

Дальше есть развилка. **Демонстрационный слепок** — дашборд заполнен сразу:

```bash
cd ~/tco && bash scripts/seed-restore.sh
```

Слепок содержит 133 сценария и трое суток наблюдений реального рынка; состав и
границы описаны в [seed/README.md](seed/README.md). Скрипт замещает содержимое
базы и отказывается работать там, где наблюдения уже накоплены.

**Либо чистый старт** — тогда нужен каталог сценариев, иначе наблюдать нечего:

```bash
sudo docker compose exec api python -m tco.cli import-scenarios catalog/monitoring_scenarios.csv
```

Первые данные появятся с ближайшим плановым сбором. Не ждать:

```bash
sudo docker compose exec api python -m tco.cli run-monitoring --limit 5
```

### 7. Проверка

Состояние сервисов и готовность API:

```bash
cd ~/tco && sudo docker compose ps && curl -fsS http://127.0.0.1:8080/api/v1/health/ready
```

Доступ к Туту и РЖД — связность проверяется изнутри контейнера, потому что в
корпоративной сети хост может видеть источник, когда контейнер уже нет:

```bash
cd ~/tco && bash scripts/check-sources.sh
```

С флагом `--live` скрипт дополнительно снимает пробный срез по одному сценарию
и показывает, что каждый источник вернул.

Забираются ли и обновляются ли данные — накопленные объемы, возраст последнего
снимка, задачи и результаты источников за сутки:

```bash
cd ~/tco && bash scripts/check-data.sh
```

Интерфейс — `http://<адрес-машины>:8080`, API — `http://<адрес-машины>:8000/api/v1`.

### Обновление развернутого стенда

```bash
cd ~/tco && bash scripts/deploy.sh --pull
```

Скрипт снимает резервную копию, подтягивает код, пересобирает образы,
применяет миграции и перезапускает сервисы. Тома он не трогает.

> **`docker compose down -v` выполнять нельзя.** Ключ `-v` удаляет тома вместе
> с накопленными наблюдениями. Для остановки — `docker compose stop`.

Резервная копия и восстановление вручную:

```bash
bash scripts/backup.sh && ls -la /srv/tco-backups
```

## Разработка

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -e ".[dev]"
```

Прогон тестов (используется SQLite, PostgreSQL не нужен):

```bash
pytest
```

Фронтенд в режиме разработки:

```bash
cd frontend && npm install && npm run dev
```

Vite проксирует `/api` на `http://localhost:8000`.

## Проверка конвейера без внешней сети

Синтетический источник-песочница прогоняет весь конвейер без обращения к
внешним API:

```bash
SANDBOX_SOURCES_ENABLED=true python -m tco.cli bootstrap
```

Расчет ведется под профилем `sandbox`: профиль `baseline` намеренно не
допускает синтетические источники. Результаты песочницы всегда помечаются
признаком `contains_synthetic_data` и не выдаются за наблюдение рынка.

## Командная строка

```bash
python -m tco.cli bootstrap            # справочники, профили, источники, пользователи
python -m tco.cli import-scenarios catalog/scenarios.csv
python -m tco.cli run-monitoring       # прогон мониторинга синхронно
python -m tco.cli source-confidence    # пересчет доверия источникам
python -m tco.cli retention            # применить политику хранения
python -m tco.cli health               # состояние подсистем
python -m tco.cli reset-password admin
```

## Документация

### Три документа, с которых стоит начать

Вся суть накопленного опыта — что ломается при работе с источниками, где число
начинает врать и почему сбор встаёт по ночам. Каждый пункт там стоил отдельного
расследования и однажды уже привёл к неверной цифре.

| Документ | О чем |
|---|---|
| [Источники Туту и РЖД](docs/SOURCES_PLAYBOOK.md) | что умеет каждый API, чем ловит, как обходить |
| [От предложения к цифре](docs/DATA_PIPELINE_PLAYBOOK.md) | где выборка портится и как проверять число |
| [Устойчивый сбор](docs/COLLECTION_RELIABILITY.md) | что ломается по ночам и как чинится |

### Остальное

| Документ | О чем |
|---|---|
| [Краткая справка](docs/EXECUTIVE_SUMMARY.md) | что сделано, что дает, какие проблемы — для руководства |
| [Методика расчета](docs/CALCULATION_METHODOLOGY.md) | как получается показатель, все пороги и формулы |
| [Особенности API источников](docs/SOURCE_API_NOTES.md) | подробный технический разбор ответов Туту и РЖД |
| [Особенности данных](docs/DATA_INSIGHTS.md) | полный журнал находок с цифрами и замерами |
| [Контракт данных](docs/DATA_CONTRACT.md) | нормализованная модель, retention, PII |
| [Архитектура](docs/ARCHITECTURE.md) | слои, потоки данных, схема развертывания |
| [Карта Celery-задач](docs/CELERY_TASK_MAP.md) | задачи, очереди, расписание, жизненный цикл |
| [Runbook](docs/RUNBOOK.md) | эксплуатация, диагностика, резервное копирование |
| [Руководство администратора](docs/ADMIN_GUIDE.md) | каталог сценариев, источники, профили |
| [Ограничения](docs/LIMITATIONS.md) | что система не делает и почему |
| [Руководство пользователя](docs/USER_GUIDE.md) | дашборд, конструктор, как читать результат |
| [Квалификация источников](docs/SOURCE_QUALIFICATION.md) | что проверено вживую и с каким решением |
| [Отчет о приемке](docs/ACCEPTANCE_REPORT.md) | соответствие Definition of Done |
| [Состояние разработки](docs/PROJECT_STATE.md) | что готово, что проверено вживую, что дальше |
| [Challenge set](docs/CHALLENGE_SET_RESULTS.md) | результаты контрольных сценариев приемки |
| [OpenAPI](docs/openapi.json) | машиночитаемый контракт API |

## Безопасность

- Секреты только через окружение; `.env` не коммитится.
- Пароли — bcrypt, доступ — JWT, роли проверяются на backend.
- Логи и аудит проходят через вычистку секретов.
- Коннекторы не принимают произвольные URL: адрес из конфигурации, хост по
  allowlist.
- Экспорт защищен от CSV injection.
- Ограничение частоты запросов включено по умолчанию.

Режим `DEPLOYMENT_MODE=OPEN` отключает авторизацию. Он допустим только как
временный, явно помечается в `/api/v1/version`, в интерфейсе и в
[ограничениях](docs/LIMITATIONS.md).

## Лицензия

Proprietary.
