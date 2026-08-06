#!/usr/bin/env bash
# Сборка демонстрационного слепка (seed/demo.dump) из рабочего стенда.
#
# Слепок нужен потому, что свежая установка показывает пустые экраны неделями:
# плановый сбор копит историю сутками, а показать надо сегодня.
#
# Рабочая база не меняется. Сборка идет через временную базу `tco_seed`: туда
# переливается копия, там из нее вычищается лишнее, оттуда снимается дамп, и
# следом временная база удаляется.
#
# Что вычищается и почему — в seed/README.md. Коротко: учетные записи, аудит,
# сырые ответы и HTML-снимки в публичный репозиторий не кладут, а предложения
# оставляются только по последнему снимку каждого сценария, иначе слепок весит
# больше гигабайта.
#
# Использование (на узле, из каталога установки):
#   bash scripts/seed-build.sh
#
# После сборки слепок нужно закоммитить вручную: каждое обновление добавляет в
# историю git еще один двоичный файл на десятки мегабайт.

set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

COMPOSE="docker compose"
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"
PG_USER="${POSTGRES_USER:-tco}"
PG_DB="${POSTGRES_DB:-tco}"
SEED_DB="tco_seed"
OUT="seed/demo.dump"

psql_admin() { $COMPOSE exec -T postgres psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 "$@"; }
psql_seed() { $COMPOSE exec -T postgres psql -U "$PG_USER" -d "$SEED_DB" -v ON_ERROR_STOP=1 "$@"; }

cleanup() {
  psql_admin -c "DROP DATABASE IF EXISTS ${SEED_DB}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== 1/5 Временная база ==="
cleanup
psql_admin -c "CREATE DATABASE ${SEED_DB}"

echo "=== 2/5 Копия рабочей базы ==="
# Через конвейер внутри контейнера: копия не покидает узел и не пишется на диск.
$COMPOSE exec -T postgres sh -c \
  "pg_dump -U ${PG_USER} -d ${PG_DB} | psql -q -U ${PG_USER} -d ${SEED_DB}" >/dev/null

echo "=== 3/5 Вычистка ==="
psql_seed <<'SQL'
BEGIN;

-- Предложения только по последнему снимку каждого сценария. Полная история —
-- сотни тысяч строк: слепок стал бы неподъемным для репозитория, а экраны,
-- которые читают предложения, показывают именно последний срез.
DELETE FROM offers WHERE market_snapshot_id NOT IN (
    SELECT DISTINCT ON (scenario_id) id
    FROM market_snapshots
    ORDER BY scenario_id, observed_at DESC
);

-- Сырые ответы и снимки страниц: выдача источников целиком, в открытом
-- репозитории ей не место. Ссылки на них обнуляются, иначе интерфейс предложит
-- открыть то, чего нет.
DELETE FROM html_snapshots;
DELETE FROM raw_responses;
UPDATE market_snapshots SET raw_response_refs = '[]'::jsonb, html_snapshot_refs = '[]'::jsonb;

-- Аудит содержит адреса и user-agent, учетные записи — хеши паролей рабочего
-- стенда. Пользователи создаются заново из BOOTSTRAP_*_PASSWORD целевой
-- установки: seed-restore.sh вызывает bootstrap сразу после восстановления.
DELETE FROM audit_events;
DELETE FROM users;

-- Кэш воспроизводится сам, срок жизни 45 минут.
DELETE FROM result_cache;

-- Журнал задач описывает работу стенда, а не состояние рынка, и на целевой
-- установке ссылался бы в пустоту.
DELETE FROM export_artifacts;
DELETE FROM job_events;
DELETE FROM jobs;

COMMIT;
VACUUM FULL;
SQL

echo "=== 4/5 Состав слепка ==="
psql_seed -t -A -F' | ' -c "
  select 'сценариев', count(*)::text from travel_scenarios where deleted_at is null
  union all select 'снимков', count(*)::text from market_snapshots
  union all select 'расчетов', count(*)::text from scenario_runs
  union all select 'предложений', count(*)::text from offers
  union all select 'период',
    coalesce(min(observation_date)::text, '-') || ' .. ' || coalesce(max(observation_date)::text, '-')
    from scenario_runs"

echo "=== 5/5 Дамп ==="
mkdir -p seed
$COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$SEED_DB" -Fc > "$OUT"
ls -lh "$OUT" | awk '{print "Слепок: " $5 " — " $9}'

echo
echo "Готово. Слепок не закоммичен: проверьте состав выше и добавьте вручную."
