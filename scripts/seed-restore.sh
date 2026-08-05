#!/usr/bin/env bash
# Поднимает базу из демонстрационного слепка (seed/demo.dump).
#
# Нужен на новом развертывании: без него дашборд пуст до первого планового
# сбора, а он идет по расписанию и накапливает историю неделями.
#
# Скрипт ЗАМЕЩАЕТ содержимое базы, поэтому по умолчанию отказывается работать
# там, где уже есть наблюдения: перепутать стенд с рабочей установкой стоит
# слишком дорого — историю цен восстановить неоткуда.
#
# Использование:
#   scripts/seed-restore.sh                  # обычный путь
#   scripts/seed-restore.sh --force          # затереть имеющиеся наблюдения
#   scripts/seed-restore.sh путь/к/файл.dump # другой слепок

set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Нет .env — скопируйте .env.example и заполните секреты" >&2; exit 1; }
set -a && . ./.env && set +a

COMPOSE="docker compose"
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"

PG_USER="${POSTGRES_USER:-tco}"
PG_DB="${POSTGRES_DB:-tco}"
SEED="seed/demo.dump"
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    -*) echo "Неизвестный аргумент: $arg" >&2; exit 1 ;;
    *) SEED="$arg" ;;
  esac
done

[ -f "$SEED" ] || { echo "Слепок не найден: $SEED" >&2; exit 1; }
echo "=== Слепок: $SEED ($(du -h "$SEED" | cut -f1)) ==="

# --- 1. База ----------------------------------------------------------------
echo "=== 1/5 Ожидание базы ==="
$COMPOSE up -d postgres redis
for _ in $(seq 1 30); do
  $COMPOSE exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1 && break
  sleep 2
done
$COMPOSE exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1 \
  || { echo "База не поднялась" >&2; exit 1; }

# --- 2. Защита от затирания --------------------------------------------------
EXISTING=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A \
  -c "select count(*) from market_snapshots" 2>/dev/null || echo 0)
if [ "${EXISTING:-0}" -gt 0 ] && [ "$FORCE" != true ]; then
  cat >&2 <<MSG
=== Отказ ===
В базе уже есть наблюдения: снимков $EXISTING.
Восстановление слепка удалит их безвозвратно — источники не отдают историю цен.
Если это действительно нужно, снимите копию и повторите с --force:
  bash scripts/backup.sh && bash scripts/seed-restore.sh --force
MSG
  exit 1
fi

# --- 3. Восстановление -------------------------------------------------------
# Сервисы останавливаются: pg_restore --clean пересоздает таблицы, а открытые
# соединения приложения держали бы на них блокировки.
echo "=== 2/5 Остановка сервисов ==="
$COMPOSE stop api worker beat ui >/dev/null

echo "=== 3/5 Восстановление ==="
$COMPOSE exec -T postgres pg_restore -U "$PG_USER" -d "$PG_DB" \
  --clean --if-exists --no-owner --no-privileges < "$SEED"

# --- 4. Схема и учетные записи ----------------------------------------------
# Слепок снят на своей версии схемы: если в репозитории миграции новее, их
# нужно применить. Пользователей в слепке нет намеренно — bootstrap создаст их
# из BOOTSTRAP_*_PASSWORD этого развертывания.
echo "=== 4/5 Миграции и учетные записи ==="
$COMPOSE --profile tools run --rm migrate
$COMPOSE --profile tools run --rm bootstrap

# --- 5. Запуск --------------------------------------------------------------
echo "=== 5/5 Запуск сервисов ==="
$COMPOSE up -d
# nginx интерфейса резолвит адрес API один раз при старте, поэтому после
# пересоздания контейнера api его нужно перечитать — иначе 502 на /api/.
$COMPOSE restart ui >/dev/null

sleep 5
echo
echo "=== Данные ==="
$COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -F" | " -c "
  select 'сценариев', count(*) from travel_scenarios
  union all select 'снимков', count(*) from market_snapshots
  union all select 'расчетов', count(*) from scenario_runs
  union all select 'предложений', count(*) from offers
  union all select 'учетных записей', count(*) from users"
echo
echo "Готово. Интерфейс — http://<адрес-машины>:${UI_PORT:-8080}"
echo "Вход — BOOTSTRAP_ADMIN_USERNAME и BOOTSTRAP_ADMIN_PASSWORD из .env."
