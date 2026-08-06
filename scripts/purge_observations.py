"""Удаление наблюдений за период.

Операция необратима и по умолчанию запрещена методикой проекта: наблюдения
невосполнимы, источники не отдают историю цен. Она нужна там, где данные не
просто неверны, а непригодны в принципе — например, собраны с неверными
параметрами запроса, и никакой пересчет их не исправит.

Сценарии не трогаются: удаляются наблюдения по ним, а сам каталог остается.
Этим операция отличается от ``purge_scenario``, которая сносит сценарий целиком.

Что переживает удаление, как и при удалении сценария:

* записи аудита — иначе теряется след самой операции;
* журнал задач — операционная история, обнуляется только ссылка;
* технические метрики источников — они описывают поведение источника, а не
  маршрут, и их потеря исказила бы историю Source Confidence.

Порядок продиктован внешними ключами: сначала то, что ссылается на снимки,
затем сами снимки. У предложений есть собственная ссылка на сценарий помимо
ссылки на снимок, поэтому каскада снимка недостаточно.

Использование:

    python scripts/purge_observations.py --before 2026-08-06 --dry-run
    python scripts/purge_observations.py --before 2026-08-06 --yes
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, update  # noqa: E402

from tco.db.models.job import Job  # noqa: E402
from tco.db.models.offer import Offer  # noqa: E402
from tco.db.models.raw import HtmlSnapshot, RawResponse  # noqa: E402
from tco.db.models.reference import ResultCacheEntry  # noqa: E402
from tco.db.models.run import ScenarioRun  # noqa: E402
from tco.db.models.snapshot import MarketSnapshot  # noqa: E402
from tco.db.models.source import SourceMetric  # noqa: E402
from tco.db.session import session_scope  # noqa: E402
from tco.storage.raw_store import get_raw_store  # noqa: E402


def parse_day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:  # noqa: BLE001 — сообщение уходит пользователю
        raise argparse.ArgumentTypeError(
            f"Ожидается дата вида 2026-08-06, получено {value!r}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Удаление наблюдений за период")
    parser.add_argument(
        "--before",
        type=parse_day,
        required=True,
        help="Удалить наблюдения строго раньше этой даты",
    )
    parser.add_argument(
        "--with-source-metrics",
        action="store_true",
        help="Снести и метрики источников (историю Source Confidence)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать объем")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтверждение: без него удаление не выполняется",
    )
    args = parser.parse_args()

    with session_scope() as session:
        snapshot_ids = list(
            session.scalars(
                select(MarketSnapshot.id).where(MarketSnapshot.observation_date < args.before)
            ).all()
        )
        runs = session.scalar(
            select(func.count(ScenarioRun.id)).where(ScenarioRun.observation_date < args.before)
        )
        offers = (
            session.scalar(
                select(func.count(Offer.id)).where(Offer.market_snapshot_id.in_(snapshot_ids))
            )
            if snapshot_ids
            else 0
        )

        print(f"Снимков: {len(snapshot_ids)}")
        print(f"Расчетов: {runs}")
        print(f"Предложений: {offers}")

        if args.dry_run:
            print("Сухой прогон: ничего не удалено")
            return 0
        if not args.yes:
            print("Не указан --yes: ничего не удалено", file=sys.stderr)
            return 2
        if not snapshot_ids:
            print("Нечего удалять")
            return 0

        # 1. Тела ответов в файловом хранилище. Ошибка удаления файла не должна
        #    останавливать операцию — иначе запись останется в БД без
        #    возможности повторить, — но обязана быть посчитана.
        raw_store = get_raw_store()
        storage_errors = 0
        refs = session.scalars(
            select(RawResponse.storage_ref).where(
                RawResponse.market_snapshot_id.in_(snapshot_ids)
            )
        ).all()
        html_refs = session.scalars(
            select(HtmlSnapshot.storage_ref).where(
                HtmlSnapshot.market_snapshot_id.in_(snapshot_ids)
            )
        ).all()
        screenshot_refs = session.scalars(
            select(HtmlSnapshot.screenshot_ref)
            .where(HtmlSnapshot.market_snapshot_id.in_(snapshot_ids))
            .where(HtmlSnapshot.screenshot_ref.is_not(None))
        ).all()
        for ref in (*refs, *html_refs, *screenshot_refs):
            if ref and not raw_store.delete(ref):
                storage_errors += 1

        # 2. Артефакты, предложения и расчеты. Предложения и итоги по источникам
        #    ушли бы и каскадом снимка, но удаляются явно — чтобы объем операции
        #    был виден в отчете, а не только в размере базы.
        session.execute(
            delete(HtmlSnapshot).where(HtmlSnapshot.market_snapshot_id.in_(snapshot_ids))
        )
        session.execute(
            delete(RawResponse).where(RawResponse.market_snapshot_id.in_(snapshot_ids))
        )
        session.execute(delete(Offer).where(Offer.market_snapshot_id.in_(snapshot_ids)))

        # 3. Журнал задач переживает удаление — обнуляется только ссылка.
        session.execute(
            update(Job)
            .where(Job.market_snapshot_id.in_(snapshot_ids))
            .values(market_snapshot_id=None, scenario_run_id=None)
        )

        session.execute(delete(ScenarioRun).where(ScenarioRun.observation_date < args.before))
        session.execute(delete(MarketSnapshot).where(MarketSnapshot.id.in_(snapshot_ids)))

        # 4. Кэш результатов ссылается на расчеты по идентификатору. Оставить его
        #    нельзя: API отдал бы из кэша ссылку на удаленный расчет.
        cache_rows = session.scalar(select(func.count(ResultCacheEntry.id))) or 0
        session.execute(delete(ResultCacheEntry))

        # 5. Метрики источников по умолчанию переживают удаление: они описывают
        #    поведение источника, а не маршрут, и их потеря искажает историю
        #    Source Confidence. Сносятся только по явному требованию — когда
        #    накоплены в том числе за время, когда сбор работал неверно.
        metric_rows = 0
        if args.with_source_metrics:
            metric_rows = session.scalar(select(func.count(SourceMetric.id))) or 0
            session.execute(delete(SourceMetric))

        print(f"Удалено снимков: {len(snapshot_ids)}, расчетов: {runs}, предложений: {offers}")
        print(f"Очищено записей кэша результатов: {cache_rows}")
        if args.with_source_metrics:
            print(f"Удалено метрик источников: {metric_rows}")
        if storage_errors:
            print(f"Не удалось удалить файлов сырых ответов: {storage_errors}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
