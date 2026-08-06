"""Снятие каталога наблюдения рынка с ежечасного сбора.

Платформа решала несколько задач сразу: наблюдение рынка по восьми городам,
произвольные сценарии поездок, витрина по пяти городам. Осталась последняя.
Каталог из 163 сценариев продолжал собираться ежечасно и занимал около трех
тысяч обращений в сутки — ровно тот запас, который нужен пагинации и
однодневным броням.

Снимается весь каталог, а не только маршруты за пределами пятерки. Витрина
строится исключительно на скользящей сетке: каталожный сценарий даже по
маршруту Москва — Сочи наблюдает другую поездку — двое взрослых, своя
длительность, проживание в том же сценарии — и в витрину не попадает ни одним
числом. Ключ ``--outside-showcase-only`` оставляет каталожные маршруты внутри
пятерки, если наблюдение рынка по ним решат сохранить.

Операция мягкая и обратимая:

* сценарии деактивируются (``is_active = false``), а не удаляются;
* накопленные снимки, расчеты и предложения остаются на месте;
* экраны каталога доступны под ролью ADMIN, данные по ним читаются;
* возврат — один запуск с ключом ``--restore``.

Сетка витрины не трогается: она отбирается признаком ``is_showcase_grid`` и
живет своим циклом.

Использование:

    python scripts/retire_catalog.py --dry-run
    python scripts/retire_catalog.py --yes
    python scripts/retire_catalog.py --restore --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from tco.core.enums import ScenarioType  # noqa: E402
from tco.db.models.reference import City  # noqa: E402
from tco.db.models.scenario import TravelScenario  # noqa: E402
from tco.db.session import session_scope  # noqa: E402
from tco.services.observation_grid import SHOWCASE_CITIES  # noqa: E402

#: Пометка в примечании сценария: снят этой операцией, а не руками.
#: Нужна для возврата — иначе восстановление подняло бы и то, что выключали
#: по другим причинам (закрытый аэропорт Анапы, стыковки Краснодар — Сочи).
MARKER = "[retired:catalog-outside-showcase]"


def main() -> int:
    parser = argparse.ArgumentParser(description="Снятие каталога вне витрины с наблюдения")
    parser.add_argument("--dry-run", action="store_true", help="Только показать объем")
    parser.add_argument("--restore", action="store_true", help="Вернуть снятое в наблюдение")
    parser.add_argument(
        "--outside-showcase-only",
        action="store_true",
        help="Снять только маршруты, у которых хотя бы один конец вне пятерки",
    )
    parser.add_argument("--yes", action="store_true", help="Подтверждение операции")
    args = parser.parse_args()

    with session_scope() as session:
        if args.restore:
            targets = [
                item
                for item in session.scalars(
                    select(TravelScenario)
                    .where(TravelScenario.is_active.is_(False))
                    .where(TravelScenario.deleted_at.is_(None))
                ).all()
                if MARKER in (item.notes or "")
            ]
            action = "вернуть в наблюдение"
        else:
            showcase_ids = set(
                session.scalars(
                    select(City.id).where(City.code.in_(SHOWCASE_CITIES))
                ).all()
            )
            catalog = session.scalars(
                select(TravelScenario)
                .where(TravelScenario.scenario_type == ScenarioType.MONITORING.value)
                .where(TravelScenario.is_active.is_(True))
                .where(TravelScenario.deleted_at.is_(None))
                .where(TravelScenario.is_showcase_grid.is_(False))
            ).all()
            # Снимается весь каталог наблюдения рынка, а не только маршруты за
            # пределами пятерки. Витрина строится исключительно на сетке:
            # каталожный сценарий даже по маршруту Москва — Сочи наблюдает
            # другую поездку (двое взрослых, своя длительность, проживание в
            # том же сценарии) и в витрину не попадает ни одним числом.
            # Наблюдение рынка — снятая задача целиком, а не ее часть.
            targets = (
                [
                    item
                    for item in catalog
                    if not (
                        item.origin_city_id in showcase_ids
                        and item.destination_city_id in showcase_ids
                    )
                ]
                if args.outside_showcase_only
                else list(catalog)
            )
            action = "снять с наблюдения"

        print(f"Сценариев {action}: {len(targets)}")
        for item in targets[:10]:
            print(f"  {item.code} — {item.name}")
        if len(targets) > 10:
            print(f"  … и еще {len(targets) - 10}")

        if args.dry_run:
            print("Сухой прогон: ничего не изменено")
            return 0
        if not args.yes:
            print("Не указан --yes: ничего не изменено", file=sys.stderr)
            return 2

        for item in targets:
            if args.restore:
                item.is_active = True
                item.notes = (item.notes or "").replace(MARKER, "").strip() or None
            else:
                item.is_active = False
                note = (item.notes or "").strip()
                item.notes = f"{note} {MARKER}".strip()[:2048]

        print(f"Готово: {len(targets)} сценариев обновлено")
        print(
            "Данные сохранены: снимки, расчеты и предложения по этим сценариям "
            "остались на месте и доступны на экранах под ролью ADMIN."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
