#!/usr/bin/env bash
# Резервная копия накопленных данных платформы.
#
# Накопленные наблюдения невосполнимы: источники не отдают историю цен,
# поэтому утраченный снимок рынка неоткуда восстановить. Резервная копия —
# единственная страховка.
#
# Копия включает:
#   * дамп PostgreSQL в custom-формате (сценарии, снимки, предложения,
#     расчеты, метрики, аудит);
#   * архив сырых ответов и HTML-снимков;
#   * манифест с версиями, контрольными суммами и счетчиками строк.
#
# Использование:
#   scripts/backup.sh [каталог_назначения]
#
# По умолчанию копии складываются в ${BACKUP_ROOT:-/srv/tco-backups}.

set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

COMPOSE="docker compose"
command -v docker >/dev/null || { echo "docker не найден" >&2; exit 1; }
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"

DEST="${1:-${BACKUP_ROOT:-/srv/tco-backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="${DEST}/${STAMP}"
PG_USER="${POSTGRES_USER:-tco}"
PG_DB="${POSTGRES_DB:-tco}"
DATA="${DATA_ROOT:-./var/data}"

mkdir -p "$DIR"
echo "Каталог копии: $DIR"

# --- База данных ------------------------------------------------------------
# Custom-формат допускает выборочное восстановление и сжат по умолчанию.
echo "1/3 Дамп PostgreSQL..."
$COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc --no-owner \
  > "$DIR/database.dump"

# --- Сырые артефакты --------------------------------------------------------
echo "2/3 Архив сырых ответов и HTML..."
if [ -d "$DATA/raw" ]; then
  tar czf "$DIR/raw-storage.tar.gz" -C "$DATA" raw
else
  echo "   каталог $DATA/raw отсутствует — пропускаю"
fi

# --- Манифест ---------------------------------------------------------------
echo "3/3 Манифест..."
COUNTS=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -F, -c "
  select 'cities', count(*) from cities
  union all select 'sources', count(*) from sources
  union all select 'scenarios', count(*) from travel_scenarios
  union all select 'snapshots', count(*) from market_snapshots
  union all select 'offers', count(*) from offers
  union all select 'runs', count(*) from scenario_runs
  union all select 'source_metrics', count(*) from source_metrics
  union all select 'audit_events', count(*) from audit_events" 2>/dev/null || echo "unavailable")

RANGE=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c \
  "select coalesce(min(observation_date)::text,'-') || ' .. ' || coalesce(max(observation_date)::text,'-') from scenario_runs" 2>/dev/null || echo "-")

{
  echo "created_at=$(date -u +%FT%TZ)"
  echo "host=$(hostname)"
  echo "compose_project=${COMPOSE_PROJECT_NAME:-tco}"
  echo "data_root=$DATA"
  echo "observation_range=$RANGE"
  echo "database_sha256=$(sha256sum "$DIR/database.dump" | cut -d' ' -f1)"
  [ -f "$DIR/raw-storage.tar.gz" ] && \
    echo "raw_sha256=$(sha256sum "$DIR/raw-storage.tar.gz" | cut -d' ' -f1)"
  echo "--- row_counts ---"
  echo "$COUNTS"
} > "$DIR/manifest.txt"

ln -sfn "$DIR" "${DEST}/latest"

echo
echo "Готово. Размер:"
du -sh "$DIR"/* 2>/dev/null || true
echo
cat "$DIR/manifest.txt"
echo
echo "Свежая копия доступна по ссылке ${DEST}/latest"
