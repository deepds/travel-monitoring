"""Покрытие наблюдений и качество суточного прогона.

Витрина показывает одно число там, где за ним стоят сотни предложений и
десяток правил отбора. Половина дефектов, найденных за неделю, была невидима
именно потому, что смотреть было некуда: обрезка выдачи на тридцати объектах,
потеря 64 % расчетов, фильтр класса вагона — все это находилось запросами к
базе руками. Этот модуль превращает такие запросы в данные для экрана.

Два разных вопроса:

* **где дыры** — матрица «маршрут × дата» и «город × дата» с состоянием каждой
  клетки и размером выборки за ней;
* **как прошел прогон** — сколько сценариев было в плане, сколько собралось,
  сколько потеряно и по какой причине.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from tco.core.enums import RunStatus, ScenarioType, SnapshotStatus
from tco.core.utils import utcnow
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot, SnapshotSourceResult
from tco.services.observation_grid import HORIZON_DAYS, SHOWCASE_CITIES

#: Размер выборки, ниже которого цифре верить нельзя: медиана по трем
#: предложениям и по сорока выглядят одинаково, а описывают рынок по-разному.
THIN_SAMPLE_THRESHOLD = 5

#: Доля потерянных сценариев, выше которой прогон считается неудачным.
FAILED_RUN_RATIO = 0.05

#: Что означает каждый технический исход обращения для цифр на витрине.
OUTCOME_MEANING: dict[str, str] = {
    "SUCCESS": "источник ответил, предложения получены",
    "EMPTY": "источник ответил, но предложений на эти даты нет",
    "TIMEOUT": "источник не ответил вовремя — наблюдения нет, рынок ни при чем",
    "RATE_LIMITED": "источник ограничил темп обращений — наблюдение отложено",
    "CIRCUIT_OPEN": "источник не опрашивался: после серии отказов обращения приостановлены",
    "SCHEMA_ERROR": "ответ источника разобрать не удалось — вероятно, он изменил формат",
    "TRANSPORT_ERROR": "обращение не состоялось из-за сети или ошибки источника",
    "AUTH_ERROR": "источник отклонил обращение по правам доступа",
    "UNSUPPORTED": "источник не отдает этот вид предложений",
    "DISABLED": "источник выключен в настройках",
}

#: Состояния клетки матрицы покрытия.
CELL_STATES: dict[str, str] = {
    "OK": "есть уверенная цифра",
    "THIN": "цифра на двух-трех предложениях",
    "PARTIAL": "выдача источника обрезана — выборка неполна",
    "NO_DATA": "наблюдение было, но пригодных предложений не нашлось",
    "NOT_OBSERVED": "наблюдения не было",
}


@dataclass(slots=True)
class _Cell:
    state: str
    run_id: str | None = None
    price: float | None = None
    offers: int = 0
    sources: int = 0


def _grid_scenarios(session: Session, *, since: date, until: date) -> list[TravelScenario]:
    return list(
        session.scalars(
            select(TravelScenario)
            .where(TravelScenario.deleted_at.is_(None))
            .where(TravelScenario.is_showcase_grid.is_(True))
            .where(TravelScenario.departure_date >= since)
            .where(TravelScenario.departure_date <= until)
        ).all()
    )


def _latest_run_ids(session: Session, scenario_ids: list[Any], observation_date: date | None) -> Select:
    """Последний расчет каждого сценария, при необходимости — на выбранную дату.

    Нумерация окном, а не ``DISTINCT ON``: последний есть только в PostgreSQL,
    а на SQLite молча вырождается в обычный ``DISTINCT`` и вернул бы всю
    историю вместо последнего среза.
    """
    query = select(
        ScenarioRun.id.label("id"),
        func.row_number()
        .over(partition_by=ScenarioRun.scenario_id, order_by=ScenarioRun.started_at.desc())
        .label("position"),
    ).where(ScenarioRun.scenario_id.in_(scenario_ids))
    if observation_date is not None:
        query = query.where(ScenarioRun.observation_date == observation_date)
    ranked = query.subquery()
    return select(ranked.c.id).where(ranked.c.position == 1)


def _cell_state(run: ScenarioRun | None, *, price: float | None, offers: int, partial: bool) -> str:
    if run is None:
        return "NOT_OBSERVED"
    if price is None:
        return "NO_DATA"
    if partial:
        return "PARTIAL"
    if offers < THIN_SAMPLE_THRESHOLD:
        return "THIN"
    return "OK"


def _partial_snapshots(session: Session, snapshot_ids: list[Any]) -> set[Any]:
    """Снимки, где хотя бы один источник отдал обрезанную выдачу."""
    if not snapshot_ids:
        return set()
    rows = session.execute(
        select(SnapshotSourceResult.market_snapshot_id)
        .where(SnapshotSourceResult.market_snapshot_id.in_(snapshot_ids))
        .where(SnapshotSourceResult.is_partial.is_(True))
    ).all()
    return {row[0] for row in rows}


def coverage_matrix(
    session: Session,
    *,
    observation_date: date | None = None,
    days: int = HORIZON_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """Матрица наблюдений: маршруты по датам отправления и города по датам заезда.

    Дыры видны глазом, а не вычитываются из графика: клетка несет состояние,
    размер выборки и ссылку на расчет, чтобы из нее можно было перейти в разбор.
    """
    today = today or utcnow().date()
    horizon = today + timedelta(days=days)
    scenarios = _grid_scenarios(session, since=today, until=horizon)
    if not scenarios:
        # Ответ по форме тот же, что при наличии данных: интерфейс читает одни
        # и те же поля, и пустая сетка не должна отличаться от полной ничем,
        # кроме пустых списков.
        return {
            "observation_date": observation_date.isoformat() if observation_date else None,
            "dates": [],
            "thin_threshold": THIN_SAMPLE_THRESHOLD,
            "transport": [],
            "accommodation": [],
            "legend": CELL_STATES,
        }

    runs = {
        run.scenario_id: run
        for run in session.scalars(
            select(ScenarioRun).where(
                ScenarioRun.id.in_(
                    _latest_run_ids(session, [item.id for item in scenarios], observation_date)
                )
            )
        ).all()
    }
    partial = _partial_snapshots(
        session, [run.market_snapshot_id for run in runs.values() if run.market_snapshot_id]
    )
    names = {
        city.code: city.name
        for city in session.scalars(select(City).where(City.code.in_(SHOWCASE_CITIES))).all()
    }

    dates = sorted({item.departure_date for item in scenarios})
    transport: dict[tuple[str, str, str], dict[str, _Cell]] = {}
    accommodation: dict[tuple[str, str], dict[str, _Cell]] = {}

    for scenario in scenarios:
        run = runs.get(scenario.id)
        is_partial = bool(run and run.market_snapshot_id in partial)
        key_date = scenario.departure_date.isoformat()
        if scenario.transport_type:
            cell = _Cell(
                state=_cell_state(
                    run,
                    price=float(run.transport_median) if run and run.transport_median else None,
                    offers=run.transport_offer_count if run else 0,
                    partial=is_partial,
                ),
                run_id=str(run.id) if run else None,
                price=float(run.transport_median) if run and run.transport_median else None,
                offers=run.transport_offer_count if run else 0,
                sources=run.transport_source_count if run else 0,
            )
            row = transport.setdefault(
                (
                    scenario.origin_city.code,
                    scenario.destination_city.code,
                    scenario.transport_type,
                ),
                {},
            )
            row[key_date] = cell
        else:
            cell = _Cell(
                state=_cell_state(
                    run,
                    price=float(run.hotel_median) if run and run.hotel_median else None,
                    offers=run.hotel_offer_count if run else 0,
                    partial=is_partial,
                ),
                run_id=str(run.id) if run else None,
                price=float(run.hotel_median) if run and run.hotel_median else None,
                offers=run.hotel_offer_count if run else 0,
                sources=run.hotel_source_count if run else 0,
            )
            row = accommodation.setdefault((scenario.destination_city.code, scenario.stars), {})
            row[key_date] = cell

    def _serialize(cells: dict[str, _Cell]) -> list[dict[str, Any]]:
        return [
            {
                "date": day.isoformat(),
                **(
                    {
                        "state": cells[day.isoformat()].state,
                        "run_id": cells[day.isoformat()].run_id,
                        "price": cells[day.isoformat()].price,
                        "offers": cells[day.isoformat()].offers,
                        "sources": cells[day.isoformat()].sources,
                    }
                    if day.isoformat() in cells
                    else {"state": "NOT_OBSERVED", "run_id": None, "price": None, "offers": 0, "sources": 0}
                ),
            }
            for day in dates
        ]

    return {
        "observation_date": observation_date.isoformat() if observation_date else None,
        "dates": [day.isoformat() for day in dates],
        "thin_threshold": THIN_SAMPLE_THRESHOLD,
        "transport": [
            {
                "origin_code": origin,
                "origin_name": names.get(origin, origin),
                "destination_code": destination,
                "destination_name": names.get(destination, destination),
                "transport_type": transport_type,
                "cells": _serialize(cells),
            }
            for (origin, destination, transport_type), cells in sorted(transport.items())
        ],
        "accommodation": [
            {
                "city_code": city,
                "city_name": names.get(city, city),
                "stars": stars,
                "cells": _serialize(cells),
            }
            for (city, stars), cells in sorted(accommodation.items())
        ],
        "legend": CELL_STATES,
    }


def daily_run_summary(session: Session, *, day: date | None = None) -> dict[str, Any]:
    """Что случилось с суточным прогоном: план, сбор, потери, отказы.

    Считается по снимкам, а не по задачам: задача может закончиться успехом,
    не получив ни одного предложения, и такой прогон нельзя называть удачным.
    """
    day = day or utcnow().date()

    planned = session.scalar(
        select(func.count())
        .select_from(TravelScenario)
        .where(TravelScenario.scenario_type == ScenarioType.MONITORING.value)
        .where(TravelScenario.is_active.is_(True))
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.is_showcase_grid.is_(True))
    ) or 0

    snapshots = session.execute(
        select(
            MarketSnapshot.status,
            func.count(func.distinct(MarketSnapshot.scenario_id)),
        )
        .where(MarketSnapshot.observation_date == day)
        .group_by(MarketSnapshot.status)
    ).all()
    by_status = {status: int(count) for status, count in snapshots}
    collected = by_status.get(SnapshotStatus.COMPLETE.value, 0) + by_status.get(
        SnapshotStatus.PARTIAL.value, 0
    )

    window = session.execute(
        select(
            func.min(MarketSnapshot.requested_at),
            func.max(MarketSnapshot.completed_at),
        ).where(MarketSnapshot.observation_date == day)
    ).one()
    started_at, finished_at = window
    duration_minutes = (
        round((finished_at - started_at).total_seconds() / 60.0, 1)
        if started_at and finished_at
        else None
    )

    snapshot_ids = select(MarketSnapshot.id).where(MarketSnapshot.observation_date == day)
    outcomes = session.execute(
        select(
            SnapshotSourceResult.source_code,
            SnapshotSourceResult.offer_type,
            SnapshotSourceResult.outcome,
            func.count(),
        )
        .where(SnapshotSourceResult.market_snapshot_id.in_(snapshot_ids))
        .group_by(
            SnapshotSourceResult.source_code,
            SnapshotSourceResult.offer_type,
            SnapshotSourceResult.outcome,
        )
    ).all()

    partial_results = session.scalar(
        select(func.count())
        .select_from(SnapshotSourceResult)
        .where(SnapshotSourceResult.market_snapshot_id.in_(snapshot_ids))
        .where(SnapshotSourceResult.is_partial.is_(True))
    ) or 0

    runs = session.execute(
        select(ScenarioRun.status, func.count())
        .where(ScenarioRun.observation_date == day)
        .group_by(ScenarioRun.status)
    ).all()
    run_statuses = {status: int(count) for status, count in runs}

    samples = session.execute(
        select(
            func.avg(ScenarioRun.transport_offer_count),
            func.avg(ScenarioRun.hotel_offer_count),
        )
        .where(ScenarioRun.observation_date == day)
        .where(ScenarioRun.status.in_([RunStatus.SUCCESS.value, RunStatus.PARTIAL_SUCCESS.value]))
    ).one()

    missing = max(0, planned - collected)
    return {
        "date": day.isoformat(),
        "planned": planned,
        "collected": collected,
        "missing": missing,
        "missing_ratio": round(missing / planned, 4) if planned else 0.0,
        "duration_minutes": duration_minutes,
        "snapshot_statuses": by_status,
        "run_statuses": run_statuses,
        "partial_source_results": int(partial_results),
        "avg_transport_offers": round(float(samples[0]), 1) if samples[0] is not None else None,
        "avg_accommodation_offers": round(float(samples[1]), 1) if samples[1] is not None else None,
        "source_outcomes": [
            {
                "source_code": source_code,
                "offer_type": offer_type,
                "outcome": outcome,
                "count": int(count),
                "meaning": OUTCOME_MEANING.get(outcome, outcome),
            }
            for source_code, offer_type, outcome, count in sorted(outcomes)
        ],
        "failed": bool(planned) and missing / planned > FAILED_RUN_RATIO,
    }
