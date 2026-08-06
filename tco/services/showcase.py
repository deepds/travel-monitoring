"""Витрина вариантов отдыха по пяти ключевым городам.

Читает наблюдения скользящей сетки (``observation_grid``) и складывает из них
две картины:

* варианты отдыха на выбранные даты — транспорт, проживание и итог по каждому
  из четырех направлений от выбранного города;
* кривые по датам отправления на горизонт вперед — отдельно транспорт по
  направлениям и проживание по городам.

Всё считается из своего хранилища, а не обращением к источникам: 20 маршрутов
на 30 дат — это минуты ожидания и неповторяемая цифра при каждом обновлении
страницы.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tco.core.enums import (
    AccommodationType,
    OfferType,
    ScenarioType,
    StarsFilter,
    TransportType,
)
from tco.core.utils import to_decimal, utcnow
from tco.db.models.offer import Offer, RailOffer
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.engine.statistics import percentile
from tco.db.models.profile import CalculationProfile
from tco.services.observation_grid import (
    CANONICAL_NIGHTS,
    GRID_PROFILE_CODE,
    GRID_TAG,
    HORIZON_DAYS,
    SHOWCASE_ADULTS,
    SHOWCASE_CITIES,
)

#: Звездность графика проживания. Массовый сегмент.
CURVE_STARS = StarsFilter.S3


@dataclass(slots=True)
class _Observation:
    """Расчет сетки вместе с параметрами своего сценария."""

    scenario: TravelScenario
    run: ScenarioRun


def _latest_runs(session: Session, scenarios: Sequence[TravelScenario]) -> dict[Any, ScenarioRun]:
    """Последний расчет каждого сценария.

    Нумерация окном, а не ``DISTINCT ON``: последний есть только в PostgreSQL,
    а на SQLite молча вырождается в обычный ``DISTINCT`` и вернул бы всю
    историю вместо последнего среза.
    """
    ids = [item.id for item in scenarios]
    if not ids:
        return {}

    ranked = select(
        ScenarioRun.id.label("id"),
        ScenarioRun.scenario_id.label("scenario_id"),
        func.row_number()
        .over(partition_by=ScenarioRun.scenario_id, order_by=ScenarioRun.started_at.desc())
        .label("position"),
    ).where(ScenarioRun.scenario_id.in_(ids)).subquery()

    latest = select(ranked.c.id).where(ranked.c.position == 1)
    runs = session.scalars(select(ScenarioRun).where(ScenarioRun.id.in_(latest))).all()
    return {run.scenario_id: run for run in runs}


def _grid_scenarios(
    session: Session,
    *,
    departure_from: date,
    departure_to: date,
) -> list[TravelScenario]:
    """Сценарии сетки в диапазоне дат отправления.

    Отбор по тегу идет в Python: ``JSONB.contains`` работает только в
    PostgreSQL, а на SQLite молча не находит ничего.
    """
    rows = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.departure_date >= departure_from)
        .where(TravelScenario.departure_date <= departure_to)
        .order_by(TravelScenario.departure_date)
    ).all()
    return [item for item in rows if GRID_TAG in (item.tags or [])]


def _on_demand_observations(
    session: Session,
    *,
    origin: str,
    departure_date: date,
    return_date: date,
    transport_type: TransportType,
    stars: StarsFilter,
) -> dict[str, _Observation]:
    """Разовые расчеты витрины на даты вне сетки.

    Кнопка «Рассчитать эти даты» заводит обычный сценарий типа ``ON_DEMAND``.
    Тега сетки у него нет и быть не должно: сетку достраивает и снимает с
    наблюдения обслуживающая задача, и чужая запись в скользящем окне сломала
    бы его. Поэтому такие расчеты читаются отдельным отбором.

    В отличие от сетки, где проезд и проживание наблюдаются разными
    сценариями, разовый расчет совмещает их в одном: он делается на конкретную
    поездку, а не на переиспользуемое наблюдение. Обе компоненты поэтому
    берутся из одного расчета — и это даже точнее, потому что они из одного
    снимка.

    Отбор строгий, вплоть до числа путешественников и звездности: витрина
    ставит числа в один ряд, и расчет с другими параметрами дал бы
    несопоставимую цифру, ничем не отличимую на вид.
    """
    origin_id = select(City.id).where(City.code == origin).scalar_subquery()
    scenarios = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.scenario_type == ScenarioType.ON_DEMAND.value)
        .where(TravelScenario.origin_city_id == origin_id)
        .where(TravelScenario.departure_date == departure_date)
        .where(TravelScenario.return_date == return_date)
        .where(TravelScenario.transport_type == transport_type.value)
        .where(TravelScenario.accommodation_type == AccommodationType.HOTEL.value)
        .where(TravelScenario.stars == stars.value)
        .where(TravelScenario.adults == SHOWCASE_ADULTS)
    ).all()

    runs = _latest_runs(session, scenarios)
    return {
        item.destination_city.code: _Observation(scenario=item, run=runs[item.id])
        for item in scenarios
        if item.id in runs
    }


def _observations(
    session: Session,
    scenarios: Iterable[TravelScenario],
) -> list[_Observation]:
    scenarios = list(scenarios)
    runs = _latest_runs(session, scenarios)
    return [
        _Observation(scenario=item, run=runs[item.id])
        for item in scenarios
        if item.id in runs
    ]


def _money(value: Any) -> float | None:
    number = to_decimal(value)
    return float(number) if number is not None else None


def _city_names(session: Session) -> dict[str, str]:
    return {
        city.code: city.name
        for city in session.scalars(select(City).where(City.code.in_(SHOWCASE_CITIES))).all()
    }


# --------------------------------------------------------------------------- #
# Блок 1. Варианты отдыха на выбранные даты
# --------------------------------------------------------------------------- #


def options(
    session: Session,
    *,
    origin: str,
    departure_date: date,
    return_date: date,
    transport_type: TransportType,
    stars: StarsFilter,
) -> dict[str, Any]:
    """Варианты отдыха из выбранного города на выбранные даты.

    Транспорт и проживание наблюдаются разными сценариями и складываются здесь:
    проживание от города отправления не зависит, и наблюдать его на каждый
    маршрут значило бы собирать одно и то же по четыре раза.
    """
    names = _city_names(session)
    scenarios = _grid_scenarios(
        session, departure_from=departure_date, departure_to=departure_date
    )
    observed = _observations(session, scenarios)

    transport: dict[str, _Observation] = {}
    accommodation: dict[str, _Observation] = {}
    for item in observed:
        scenario = item.scenario
        if scenario.return_date != return_date:
            continue
        if scenario.transport_type == transport_type.value:
            if scenario.origin_city.code == origin:
                transport[scenario.destination_city.code] = item
        elif scenario.transport_type is None and scenario.stars == stars.value:
            accommodation[scenario.destination_city.code] = item

    # Даты вне сетки покрываются разовым расчетом — иначе кнопка «Рассчитать
    # эти даты» отрабатывала бы вхолостую: расчет проходил, а витрина его не
    # видела и показывала ту же пустую таблицу.
    on_demand = _on_demand_observations(
        session,
        origin=origin,
        departure_date=departure_date,
        return_date=return_date,
        transport_type=transport_type,
        stars=stars,
    )

    items: list[dict[str, Any]] = []
    for code in SHOWCASE_CITIES:
        if code == origin:
            continue
        transport_run = transport.get(code)
        stay_run = accommodation.get(code)
        transport_cost = _money(transport_run.run.transport_median) if transport_run else None
        stay_cost = _money(stay_run.run.hotel_median) if stay_run else None

        # Наблюдение сетки в приоритете: разовый расчет дополняет его, а не
        # подменяет. Иначе один пересчет менял бы цифру уже показанного ряда.
        once = on_demand.get(code)
        from_on_demand = False
        if once is not None:
            if transport_cost is None:
                transport_cost = _money(once.run.transport_median)
                from_on_demand = transport_cost is not None
            if stay_cost is None:
                stay_cost = _money(once.run.hotel_median)
                from_on_demand = from_on_demand or stay_cost is not None

        total = (
            transport_cost + stay_cost
            if transport_cost is not None and stay_cost is not None
            else None
        )
        items.append(
            {
                "destination_code": code,
                "destination_name": names.get(code, code),
                "transport": transport_cost,
                "accommodation": stay_cost,
                "total": total,
                # Разовый расчет — это один снимок по запросу, а не наблюдение
                # сетки. Интерфейс должен иметь возможность это показать.
                "on_demand": from_on_demand,
                # Показываем то, чего не хватило: пустая строка без объяснения
                # читается как «поездка бесплатна».
                "missing": [
                    name
                    for name, value in (("transport", transport_cost), ("accommodation", stay_cost))
                    if value is None
                ],
            }
        )

    # Идентификатор методики нужен интерфейсу: разовый расчет для дат вне сетки
    # должен считаться теми же правилами, иначе его цифра несопоставима с
    # остальной витриной.
    profile = session.scalars(
        select(CalculationProfile).where(CalculationProfile.code == GRID_PROFILE_CODE)
    ).first()

    return {
        "origin_code": origin,
        "origin_name": names.get(origin, origin),
        "profile_id": str(profile.id) if profile else None,
        "departure_date": departure_date.isoformat(),
        "return_date": return_date.isoformat(),
        "nights": (return_date - departure_date).days,
        "transport_type": transport_type.value,
        "stars": stars.value,
        "travelers": SHOWCASE_ADULTS,
        "items": items,
        "available": any(item["total"] is not None for item in items),
        "grid_nights": CANONICAL_NIGHTS,
    }


# --------------------------------------------------------------------------- #
# Блок 2. Кривые по датам отправления
# --------------------------------------------------------------------------- #


def _one_way_medians(
    session: Session, snapshot_ids: Sequence[Any]
) -> dict[Any, float]:
    """Медиана цены поездки в одну сторону по каждому снимку.

    Расчет хранит только круговую стоимость, поэтому одностороннюю приходится
    считать из предложений. Отдельно наблюдать поездку в один конец не нужно:
    цена плеча «туда» и так лежит у ЖД-предложения своим полем, а коннектор
    запрашивает плечи по отдельности.

    Берутся только предложения, попавшие в расчет: то же множество, по которому
    считается круговая медиана.
    """
    if not snapshot_ids:
        return {}

    rows = session.execute(
        select(Offer.market_snapshot_id, RailOffer.price_per_place_outbound)
        .join(RailOffer, RailOffer.offer_id == Offer.id)
        .where(Offer.market_snapshot_id.in_(list(snapshot_ids)))
        .where(Offer.offer_type == OfferType.RAIL.value)
        .where(Offer.exclusion_reason == "NONE")
        .where(RailOffer.price_per_place_outbound.is_not(None))
    ).all()

    buckets: dict[Any, list[Decimal]] = {}
    for snapshot_id, price in rows:
        value = to_decimal(price)
        if value is not None:
            buckets.setdefault(snapshot_id, []).append(value)

    medians: dict[Any, float] = {}
    for snapshot_id, prices in buckets.items():
        median = percentile(sorted(prices), 0.5, "LINEAR")
        if median is not None:
            # Цена за место, а состав тургруппы задан методикой витрины.
            medians[snapshot_id] = float(median) * SHOWCASE_ADULTS
    return medians


def transport_curve(
    session: Session,
    *,
    origin: str,
    days: int = HORIZON_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """Цена поездки в одну сторону по датам отправления, ЖД.

    Отвечает на вопрос «сколько стоит выехать сегодня, завтра, через неделю» —
    это глубина бронирования, а не движение цены во времени: вся кривая
    собирается из одного цикла наблюдений.
    """
    today = today or utcnow().date()
    horizon = today + timedelta(days=days)
    names = _city_names(session)

    scenarios = [
        item
        for item in _grid_scenarios(session, departure_from=today, departure_to=horizon)
        if item.transport_type == TransportType.RAIL.value
        and item.origin_city.code == origin
    ]
    observed = _observations(session, scenarios)
    medians = _one_way_medians(
        session, [item.run.market_snapshot_id for item in observed if item.run.market_snapshot_id]
    )

    series: dict[str, list[dict[str, Any]]] = {
        code: [] for code in SHOWCASE_CITIES if code != origin
    }
    for item in observed:
        code = item.scenario.destination_city.code
        if code not in series:
            continue
        price = medians.get(item.run.market_snapshot_id)
        if price is None:
            continue
        series[code].append(
            {"departure_date": item.scenario.departure_date.isoformat(), "price": price}
        )

    return {
        "origin_code": origin,
        "origin_name": names.get(origin, origin),
        "transport_type": TransportType.RAIL.value,
        "direction": "ONE_WAY",
        "travelers": SHOWCASE_ADULTS,
        "horizon_days": days,
        "series": [
            {
                "destination_code": code,
                "destination_name": names.get(code, code),
                "points": sorted(points, key=lambda point: point["departure_date"]),
            }
            for code, points in series.items()
        ],
    }


def accommodation_curve(
    session: Session,
    *,
    stars: StarsFilter = CURVE_STARS,
    days: int = HORIZON_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """Медиана проживания по датам заезда для каждого из пяти городов."""
    today = today or utcnow().date()
    horizon = today + timedelta(days=days)
    names = _city_names(session)

    scenarios = [
        item
        for item in _grid_scenarios(session, departure_from=today, departure_to=horizon)
        if item.transport_type is None and item.stars == stars.value
    ]
    observed = _observations(session, scenarios)

    series: dict[str, list[dict[str, Any]]] = {code: [] for code in SHOWCASE_CITIES}
    for item in observed:
        code = item.scenario.destination_city.code
        price = _money(item.run.hotel_median)
        if code not in series or price is None:
            continue
        series[code].append(
            {"check_in": item.scenario.departure_date.isoformat(), "price": price}
        )

    return {
        "stars": stars.value,
        "nights": CANONICAL_NIGHTS,
        "travelers": SHOWCASE_ADULTS,
        "horizon_days": days,
        "series": [
            {
                "city_code": code,
                "city_name": names.get(code, code),
                "points": sorted(points, key=lambda point: point["check_in"]),
            }
            for code, points in series.items()
        ],
    }
