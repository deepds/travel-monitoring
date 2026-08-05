#!/usr/bin/env bash
# Проверка доступа к источникам данных.
#
# Проверяет связность именно ИЗ КОНТЕЙНЕРА, а не с хоста: в корпоративной сети
# наружу обычно ходят через прокси, и хост может видеть источник, когда
# контейнер уже нет.
#
# Использование:
#   scripts/check-sources.sh          # связность и состояние источников
#   scripts/check-sources.sh --live   # плюс реальный сбор по одному сценарию

set -uo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

COMPOSE="docker compose"
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"
PG_USER="${POSTGRES_USER:-tco}"
PG_DB="${POSTGRES_DB:-tco}"
LIVE=false
[ "${1:-}" = "--live" ] && LIVE=true

fail=0

# --- 1. Связность ------------------------------------------------------------
# Источники отвечают на корень чем угодно — 200, 403, 405. Значение имеет сам
# факт установленного TLS-соединения: код ответа тут ничего не говорит о том,
# работает ли API, это показывает только реальный сбор ниже.
echo "=== 1/3 Связность из контейнера api ==="
printf '%-24s %-8s %-10s %s\n' ИСТОЧНИК КОД ВРЕМЯ АДРЕС
for pair in "tutu_mcp:${TUTU_MCP_URL:-https://mcp.tutu.ru/mcp}" \
            "rzd:${RZD_BASE_URL:-https://ticket.rzd.ru}"; do
  name="${pair%%:*}"
  url="${pair#*:}"
  read -r code time <<<"$($COMPOSE exec -T api \
      curl -s -o /dev/null -m 20 -w '%{http_code} %{time_total}' "$url" 2>/dev/null || echo '000 -')"
  if [ "$code" = "000" ]; then
    printf '%-24s %-8s %-10s %s  <- НЕТ СВЯЗИ\n' "$name" "$code" "$time" "$url"
    fail=1
  else
    printf '%-24s %-8s %-10s %s\n' "$name" "$code" "${time}s" "$url"
  fi
done

if [ "$fail" = 1 ]; then
  cat <<'MSG'

Связи нет. Обычные причины в корпоративной сети:
  * наружу только через прокси — задайте HTTP_PROXY/HTTPS_PROXY в .env
    и перезапустите: docker compose up -d
  * домен закрыт политикой — нужен доступ к mcp.tutu.ru и ticket.rzd.ru
  * нет DNS в контейнере — проверьте: docker compose exec api getent hosts mcp.tutu.ru
MSG
fi

# --- 2. Состояние источников по данным платформы ----------------------------
# Предохранитель размыкается после серии отказов и закрывает источник на
# CONNECTOR_CIRCUIT_BREAKER_COOLDOWN_SECONDS — при живой сети это главный
# повод для пустых снимков.
echo
echo "=== 2/3 Состояние источников ==="
$COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -F' | ' -c "
  select code,
         case when is_enabled then 'включен' else 'ВЫКЛЮЧЕН' end,
         'отказов подряд: ' || consecutive_failures,
         case when circuit_open_until is null or circuit_open_until < now()
              then 'предохранитель замкнут'
              else 'ПРЕДОХРАНИТЕЛЬ РАЗОМКНУТ до ' || circuit_open_until::text end,
         'последний успех: ' || coalesce(last_success_at::text, 'не было'),
         coalesce('последняя ошибка: ' || left(last_error, 80), '')
  from sources where is_enabled or last_failure_at is not null
  order by code" 2>/dev/null || echo "(база недоступна)"

# --- 3. Реальный сбор --------------------------------------------------------
echo
if [ "$LIVE" = true ]; then
  echo "=== 3/3 Пробный сбор по одному сценарию ==="

  # Отсчет берется из базы, а не из `date` на хосте: часы контейнера и хоста
  # расходятся, и по хостовому времени результаты сбора не находились бы.
  START=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c 'select now()')

  # Команда ставит задачу в очередь и сразу возвращает DISPATCHED — читать
  # результаты сразу после нее бессмысленно, их еще не записал воркер.
  $COMPOSE exec -T api python -m tco.cli run-monitoring --limit 1 --force >/dev/null 2>&1 \
    || { echo "Не удалось поставить задачу — жив ли worker? $COMPOSE ps worker" >&2; exit 1; }

  printf 'Задача поставлена, идет обращение к источникам '
  done_at=""
  for _ in $(seq 1 40); do
    pending=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c "
      select count(*) from jobs
      where created_at >= '$START'
        and status not in ('SUCCESS','FAILED','CANCELLED','TIMED_OUT','PARTIAL')" 2>/dev/null || echo 1)
    started=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c "
      select count(*) from jobs where created_at >= '$START'" 2>/dev/null || echo 0)
    if [ "${started:-0}" -gt 0 ] && [ "${pending:-1}" -eq 0 ]; then done_at=ok; break; fi
    printf '.'
    sleep 3
  done
  echo

  if [ -z "$done_at" ]; then
    echo "Сбор не завершился за 2 минуты. Смотрите: $COMPOSE logs --tail 50 worker"
    fail=1
  fi

  echo
  echo "Что вернули источники:"
  $COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -F' | ' -c "
    select r.source_code, r.outcome,
           'предложений: ' || r.valid_offer_count,
           'задержка: ' || coalesce(r.latency_ms::text, '-') || ' мс',
           coalesce(left(r.error_message, 60), '')
    from snapshot_source_results r
    where r.collected_at >= '$START'
    order by r.source_code" 2>/dev/null || echo "(нет данных)"
  [ -z "$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c \
      "select 1 from snapshot_source_results where collected_at >= '$START' limit 1" 2>/dev/null)" ] \
    && echo "(источники не ответили ни разу — смотрите логи воркера)" && fail=1
else
  echo "=== 3/3 Пробный сбор пропущен ==="
  echo "Чтобы проверить сбор вживую: bash scripts/check-sources.sh --live"
fi

exit "$fail"
