#!/usr/bin/env bash
# Проверка того, что данные забираются и обновляются.
#
# Отвечает на два вопроса: сколько накоплено и когда обновлялось в последний
# раз. Свежесть считается относительно SNAPSHOT_INTERVAL_HOURS с запасом:
# плановый сбор идет пачками и растягивается на десятки минут.
#
# Использование: scripts/check-data.sh

set -uo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

COMPOSE="docker compose"
docker info >/dev/null 2>&1 || COMPOSE="sudo docker compose"
PG_USER="${POSTGRES_USER:-tco}"
PG_DB="${POSTGRES_DB:-tco}"
INTERVAL="${SNAPSHOT_INTERVAL_HOURS:-1}"
# Допуск: два интервала плюс час на длительность самого прогона.
STALE_AFTER=$(( INTERVAL * 2 + 1 ))

psql_q() { $COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A "$@" 2>/dev/null; }

echo "=== 1/4 Накоплено ==="
psql_q -F' | ' -c "
  select 'сценариев (активных)', count(*) filter (where is_active and deleted_at is null) || ' из ' || count(*)
    from travel_scenarios
  union all select 'снимков рынка', count(*)::text from market_snapshots
  union all select 'расчетов', count(*)::text from scenario_runs
  union all select 'предложений', count(*)::text from offers
  union all select 'период наблюдений',
    coalesce(min(observation_date)::text, '-') || ' .. ' || coalesce(max(observation_date)::text, '-')
    from scenario_runs" || { echo "(база недоступна)"; exit 1; }

echo
echo "=== 2/4 Свежесть ==="
AGE=$(psql_q -c "select coalesce(round(extract(epoch from now() - max(observed_at)) / 60)::text, 'нет') from market_snapshots")
psql_q -F' | ' -c "
  select 'последний снимок', coalesce(max(observed_at)::text, 'не было') from market_snapshots
  union all select 'последний расчет', coalesce(max(started_at)::text, 'не было') from scenario_runs
  union all select 'последнее предложение', coalesce(max(collected_at)::text, 'не было') from offers"

echo
if [ "$AGE" = "нет" ]; then
  echo "ДАННЫХ НЕТ. Плановый сбор идет раз в $INTERVAL ч, первый снимок появится по расписанию."
  echo "Снять срез немедленно: $COMPOSE exec api python -m tco.cli run-monitoring --limit 5"
elif [ "$AGE" -gt $(( STALE_AFTER * 60 )) ]; then
  echo "УСТАРЕЛО: последний снимок $AGE мин назад при интервале $INTERVAL ч."
  echo "Смотрите ошибки: bash scripts/check-sources.sh и $COMPOSE logs --tail 50 worker beat"
else
  echo "Свежо: последний снимок $AGE мин назад при интервале $INTERVAL ч."
fi

echo
echo "=== 3/4 Задачи за сутки ==="
psql_q -F' | ' -c "
  select status, count(*)::text, 'последняя: ' || max(created_at)::text
  from jobs where created_at > now() - interval '24 hours'
  group by status order by count(*) desc"
[ -z "$(psql_q -c "select 1 from jobs where created_at > now() - interval '24 hours' limit 1")" ] \
  && echo "(за сутки задач не было — проверьте, жив ли планировщик: $COMPOSE ps beat)"

echo
echo "=== 4/4 Источники за сутки ==="
# Пустой результат при живой сети означает, что сбор не доходил до источников.
psql_q -F' | ' -c "
  select source_code, outcome, count(*)::text,
         'предложений: ' || coalesce(sum(valid_offer_count), 0)
  from snapshot_source_results
  where collected_at > now() - interval '24 hours'
  group by source_code, outcome order by source_code, count(*) desc"
