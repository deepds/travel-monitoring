#!/usr/bin/env bash
# Восстановление данных платформы из резервной копии.
#
# Сценарии применения:
#   * авария на стенде;
#   * перенос накопленных наблюдений на новое окружение;
#   * поднятие тестового стенда «от релевантного слепка», а не с нуля.
#
# Восстановление ЗАМЕЩАЕТ содержимое базы. Скрипт требует явного
# подтверждения и по умолчанию делает страховочную копию текущего состояния.
#
# Использование:
#   scripts/restore.sh /srv/tco-backups/20260804T021500Z
#   scripts/restore.sh /srv/tco-backups/latest --yes

set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

SRC="${1:-}"
ASSUME_YES="${2:-}"
[ -n "$SRC" ] || { echo "Укажите каталог копии: scripts/restore.sh <dir> [--yes]" >&2; exit 1; }
[ -f "$SRC/database.dump" ] || { echo "Не найден $SRC/database.dump" >&2; exit 1; }

COMPOSE="docker compose"
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"
PG_USER="${POSTGRES_USER:-tco}"
PG_DB="${POSTGRES_DB:-tco}"
DATA="${DATA_ROOT:-./var/data}"

echo "=== Копия ==="
cat "$SRC/manifest.txt" 2>/dev/null || echo "(манифест отсутствует)"
echo

# Контрольная сумма: молчаливое восстановление битого дампа хуже отказа.
if grep -q '^database_sha256=' "$SRC/manifest.txt" 2>/dev/null; then
  EXPECTED=$(grep '^database_sha256=' "$SRC/manifest.txt" | cut -d= -f2)
  ACTUAL=$(sha256sum "$SRC/database.dump" | cut -d' ' -f1)
  if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "ОШИБКА: контрольная сумма дампа не совпадает с манифестом" >&2
    exit 1
  fi
  echo "Контрольная сумма дампа совпадает."
fi

if [ "$ASSUME_YES" != "--yes" ]; then
  echo
  echo "ВНИМАНИЕ: текущее содержимое базы «$PG_DB» будет замещено."
  read -r -p "Продолжить? введите YES: " CONFIRM
  [ "$CONFIRM" = "YES" ] || { echo "Отменено."; exit 1; }
fi

# --- Страховочная копия текущего состояния ---------------------------------
SAFETY="${BACKUP_ROOT:-/srv/tco-backups}/pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
echo "Страховочная копия текущего состояния: $SAFETY"
mkdir -p "$SAFETY"
$COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc --no-owner \
  > "$SAFETY/database.dump" 2>/dev/null || echo "   (текущая база недоступна — пропускаю)"

# --- Останавливаем потребителей, БД оставляем поднятой ---------------------
echo "Останавливаю api/worker/beat..."
$COMPOSE stop api worker beat >/dev/null 2>&1 || true

echo "Восстанавливаю базу..."
$COMPOSE exec -T postgres pg_restore -U "$PG_USER" -d "$PG_DB" \
  --clean --if-exists --no-owner < "$SRC/database.dump"

if [ -f "$SRC/raw-storage.tar.gz" ]; then
  echo "Восстанавливаю сырые артефакты..."
  mkdir -p "$DATA"
  tar xzf "$SRC/raw-storage.tar.gz" -C "$DATA"
fi

# Схема копии может быть старше кода: миграции доводят ее до текущей версии.
echo "Применяю миграции..."
$COMPOSE --profile tools run --rm migrate

echo "Запускаю сервисы..."
$COMPOSE up -d

echo
echo "=== Проверка ==="
$COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -F" | " -c "
  select 'сценариев', count(*) from travel_scenarios
  union all select 'снимков', count(*) from market_snapshots
  union all select 'расчетов', count(*) from scenario_runs
  union all select 'предложений', count(*) from offers"
echo
echo "Восстановление завершено. Страховочная копия: $SAFETY"
