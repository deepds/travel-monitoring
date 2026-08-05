#!/usr/bin/env bash
# Безопасное обновление стенда с сохранением накопленных данных.
#
# Порядок намеренно консервативный: сначала резервная копия, затем сборка,
# затем миграции, и только потом перезапуск сервисов. Тома не удаляются
# никогда — `docker compose down -v` в этом скрипте отсутствует и не должен
# выполняться на рабочем стенде вручную.
#
# Использование:
#   scripts/deploy.sh                 # обновить из текущего рабочего каталога
#   scripts/deploy.sh --pull          # сначала git pull
#   scripts/deploy.sh --skip-backup   # без копии (только для чистого стенда)

set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Нет .env — скопируйте .env.example и заполните секреты" >&2; exit 1; }
set -a && . ./.env && set +a

COMPOSE="docker compose"
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"
DATA="${DATA_ROOT:-./var/data}"
DO_PULL=false
DO_BACKUP=true
for arg in "$@"; do
  case "$arg" in
    --pull) DO_PULL=true ;;
    --skip-backup) DO_BACKUP=false ;;
    *) echo "Неизвестный аргумент: $arg" >&2; exit 1 ;;
  esac
done

echo "=== Каталог данных: $DATA ==="
sudo mkdir -p "$DATA"/{pgdata,raw,exports} 2>/dev/null || mkdir -p "$DATA"/{pgdata,raw,exports}

# --- 1. Резервная копия -----------------------------------------------------
# Копия снимается ДО любых изменений: если обновление пойдет не так,
# откатываться будет к чему. Наблюдения невосполнимы.
if [ "$DO_BACKUP" = true ]; then
  if $COMPOSE ps --status running 2>/dev/null | grep -q postgres; then
    echo "=== 1/5 Резервная копия ==="
    ./scripts/backup.sh || { echo "Копия не создана — обновление остановлено" >&2; exit 1; }
  else
    echo "=== 1/5 Резервная копия: база не запущена, пропускаю ==="
  fi
else
  echo "=== 1/5 Резервная копия пропущена по флагу ==="
fi

# --- 2. Код -----------------------------------------------------------------
if [ "$DO_PULL" = true ]; then
  echo "=== 2/5 Обновление кода ==="
  git pull --ff-only
else
  echo "=== 2/5 Обновление кода пропущено ==="
fi

# --- 3. Сборка --------------------------------------------------------------
echo "=== 3/5 Сборка образов ==="
$COMPOSE build

# --- 4. Миграции ------------------------------------------------------------
# Только вперед и без удаления данных. Выполняются до перезапуска сервисов,
# чтобы новый код не встретил старую схему.
echo "=== 4/5 Миграции ==="
$COMPOSE --profile tools run --rm migrate

# --- 5. Перезапуск ----------------------------------------------------------
echo "=== 5/5 Перезапуск сервисов ==="
$COMPOSE up -d

# Контейнер ui пересоздается только при изменении фронтенда, а адрес api
# меняется при каждом его пересоздании. Перезапуск нужен, чтобы nginx перечитал
# адрес: иначе после правки одного бэкенда интерфейс отвечает 502 на /api/.
$COMPOSE restart ui >/dev/null

sleep 10
echo
echo "=== Состояние ==="
$COMPOSE ps --format '{{.Service}}  {{.Status}}'
echo
echo "=== Накопленные данные ==="
$COMPOSE exec -T postgres psql -U "${POSTGRES_USER:-tco}" -d "${POSTGRES_DB:-tco}" -t -A -F" | " -c "
  select 'сценариев', count(*) from travel_scenarios
  union all select 'снимков', count(*) from market_snapshots
  union all select 'расчетов', count(*) from scenario_runs
  union all select 'предложений', count(*) from offers" 2>/dev/null || echo "(база еще недоступна)"

echo
# API_PORT может задавать интерфейс — «127.0.0.1:8000», чтобы порт не смотрел
# в сеть. В URL нужен только номер, иначе curl отвергает адрес целиком.
HEALTH_PORT="${API_PORT:-8000}"
curl -fsS "http://127.0.0.1:${HEALTH_PORT##*:}/api/v1/health/ready" && echo \
  || echo "health/ready пока не отвечает"
