"""Массовый пересчет снимков рынка действующей методикой.

Зачем. Снимок рынка неизменяем и хранит предложения так, как их отдал источник.
Расчет — отдельная сущность поверх него. Поэтому исправление правил отбора или
новая версия профиля не требуют повторного обращения к источникам: наблюдения
уже есть, их нужно только пересчитать. Иначе неверные числа пришлось бы либо
терпеть, либо удалять вместе с наблюдениями, а наблюдения невосполнимы —
источники не отдают историю цен.

Что делает. Для каждого снимка в диапазоне дат наблюдения ставит задачу
``replay_snapshot_with_profile``. Она создает новый ``ScenarioRun``, не трогая
снимок и не удаляя прежний расчет: история пересчетов сохраняется, а дашборд
показывает последний. Дата наблюдения берется из снимка, поэтому исправленные
числа встают на свои места в истории, а не сваливаются в сегодняшний день.

Методика выбирается той же функцией, что и в плановом расчете
(``active_profile``): у сценария сетки витрины это ее профиль, у остального
каталога — базовый. Задавать профиль вручную нужно только для разбора «а что
было бы по другим правилам» — для этого есть ``--profile-code``.

Обращений к источникам скрипт не делает вообще: задачи идут в очередь расчета.

Использование:

    python scripts/replay_snapshots.py --from 2026-08-04 --to 2026-08-05
    python scripts/replay_snapshots.py --from 2026-08-04 --to 2026-08-05 --dry-run
    python scripts/replay_snapshots.py --from 2026-08-04 --to 2026-08-04 --limit 50

Повторный запуск создаст еще по одному расчету на снимок: идемпотентности здесь
нет намеренно — пересчет по определению делается заново. Поэтому сначала стоит
посмотреть на ``--dry-run``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from tco.db.models.profile import CalculationProfile  # noqa: E402
from tco.db.models.run import ScenarioRun  # noqa: E402
from tco.db.models.scenario import TravelScenario  # noqa: E402
from tco.db.models.snapshot import MarketSnapshot  # noqa: E402
from tco.db.session import session_scope  # noqa: E402
from tco.services.calculation import active_profile  # noqa: E402


def parse_day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:  # noqa: BLE001 — сообщение уходит пользователю
        raise argparse.ArgumentTypeError(f"Ожидается дата вида 2026-08-04, получено {value!r}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Пересчет снимков действующей методикой")
    parser.add_argument("--from", dest="date_from", type=parse_day, required=True)
    parser.add_argument("--to", dest="date_to", type=parse_day, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число снимков")
    parser.add_argument(
        "--profile-code",
        default=None,
        help="Считать всё этой методикой вместо той, что закреплена за сценарием",
    )
    parser.add_argument(
        "--redo-failed",
        action="store_true",
        help="Только снимки, чей последний расчет FAILED или NO_DATA",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет сделано")
    args = parser.parse_args()

    if args.date_to < args.date_from:
        print("Конец диапазона раньше начала", file=sys.stderr)
        return 2

    with session_scope() as session:
        forced = None
        if args.profile_code:
            forced = session.scalars(
                select(CalculationProfile)
                .where(CalculationProfile.code == args.profile_code)
                .where(CalculationProfile.status == "ACTIVE")
                .order_by(CalculationProfile.version_seq.desc())
            ).first()
            if forced is None:
                print(f"Действующая методика {args.profile_code!r} не найдена", file=sys.stderr)
                return 2

        stmt = (
            select(MarketSnapshot)
            .where(MarketSnapshot.observation_date >= args.date_from)
            .where(MarketSnapshot.observation_date <= args.date_to)
            .order_by(MarketSnapshot.observed_at)
        )
        if args.limit:
            stmt = stmt.limit(args.limit)
        snapshots = session.scalars(stmt).all()

        # Повтор после неудачного пересчета: гонять заново весь диапазон незачем,
        # а по одному снимку — слишком долго. Снимки без данных в источнике сюда
        # тоже попадут и снова дадут NO_DATA — это дешевле, чем отличать их от
        # неудач по причине.
        failed_only: set = set()
        if args.redo_failed:
            latest = (
                select(ScenarioRun.market_snapshot_id, func.max(ScenarioRun.started_at))
                .where(ScenarioRun.market_snapshot_id.is_not(None))
                .group_by(ScenarioRun.market_snapshot_id)
                .subquery()
            )
            failed_only = {
                row[0]
                for row in session.execute(
                    select(ScenarioRun.market_snapshot_id)
                    .join(
                        latest,
                        (ScenarioRun.market_snapshot_id == latest.c.market_snapshot_id)
                        & (ScenarioRun.started_at == latest.c.max_1),
                    )
                    .where(ScenarioRun.status.in_(("FAILED", "NO_DATA")))
                ).all()
            }

        planned: list[tuple[str, str]] = []
        by_profile: dict[str, int] = {}
        skipped_purged = 0
        for snapshot in snapshots:
            if args.redo_failed and snapshot.id not in failed_only:
                continue
            # Предложения могли быть вычищены политикой хранения: пересчитывать
            # тогда нечего, и задача упала бы с ошибкой на каждом таком снимке.
            if not snapshot.offers_available:
                skipped_purged += 1
                continue
            scenario = session.get(TravelScenario, snapshot.scenario_id)
            profile = forced or active_profile(session, scenario)
            if profile is None:
                skipped_purged += 1
                continue
            planned.append((str(snapshot.id), str(profile.id)))
            by_profile[profile.label] = by_profile.get(profile.label, 0) + 1

    print(f"Снимков в диапазоне: {len(snapshots)}")
    if skipped_purged:
        print(f"Пропущено (предложения вычищены или нет методики): {skipped_purged}")
    for label, count in sorted(by_profile.items()):
        print(f"  {label}: {count}")

    if args.dry_run:
        print("Сухой прогон: задачи не поставлены")
        return 0

    from tco.tasks.pipeline import replay_snapshot_with_profile

    for snapshot_id, profile_id in planned:
        replay_snapshot_with_profile.apply_async(
            kwargs={
                "snapshot_id": snapshot_id,
                "profile_id": profile_id,
                "created_by": "replay-script",
            }
        )

    print(f"Поставлено задач пересчета: {len(planned)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
