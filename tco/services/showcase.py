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

ДВЕ ЦИФРЫ ВМЕСТО ОДНОЙ

Медиана систематически выше того, что платит покупатель: человек едет
туда-обратно одним поездом и берет из дешевой половины выдачи. Поэтому рядом с
ней всегда идет минимум — «от 12 400 ₽, типично 18 700 ₽». Это же снимает
главное расхождение с сайтом источника: сайт крупно показывает «от», и пока
рядом стоит одна медиана, разница читается как ошибка. По Казани минимум
1 260 ₽ при медиане около 6 900 — расхождение в пять раз на ровном месте.

Важная разница в природе этих чисел. По транспорту и проживанию «от» — это
конкретное наблюдавшееся предложение. По итогу — сумма минимумов, то есть
«самый дешевый билет плюс самый дешевый отель»: комбинация, которую человек
может и не собрать, потому что дешевый рейс бывает в неудобное время, а
дешевый отель к тому моменту разберут. Итоговое «от» — нижняя граница, а не
предложение, и подписывать его надо соответственно.

СКОЛЬКО ПРЕДЛОЖЕНИЙ СТОИТ ЗА ЦИФРОЙ

Медиана по четырем предложениям и медиана по сорока выглядят одинаково, а
доверия заслуживают разного. Размер выборки и число источников идут вместе с
ценой везде, где она показывается.
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
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.engine.statistics import percentile
from tco.services.observation_grid import (
    CANONICAL_NIGHTS,
    GRID_PROFILE_CODE,
    HORIZON_DAYS,
    SHOWCASE_ADULTS,
    SHOWCASE_CITIES,
    STAY_NIGHTS,
)

#: Звездность графика проживания. Массовый сегмент.
CURVE_STARS = StarsFilter.S3


@dataclass(slots=True)
class _Observation:
    """Расчет сетки вместе с параметрами своего сценария."""

    scenario: TravelScenario
    run: ScenarioRun


def _latest_runs(
    session: Session,
    scenarios: Sequence[TravelScenario],
    *,
    observation_date: date | None = None,
) -> dict[Any, ScenarioRun]:
    """Последний расчет каждого сценария, при необходимости — на выбранную дату.

    ``observation_date`` позволяет посмотреть кривую такой, какой она была
    вчера: наблюдения не переписываются, и вчерашний срез никуда не делся —
    без отбора по дате мы просто всегда брали последний расчет вообще.

    Нумерация окном, а не ``DISTINCT ON``: последний есть только в PostgreSQL,
    а на SQLite молча вырождается в обычный ``DISTINCT`` и вернул бы всю
    историю вместо последнего среза.
    """
    ids = [item.id for item in scenarios]
    if not ids:
        return {}

    query = select(
        ScenarioRun.id.label("id"),
        ScenarioRun.scenario_id.label("scenario_id"),
        func.row_number()
        .over(partition_by=ScenarioRun.scenario_id, order_by=ScenarioRun.started_at.desc())
        .label("position"),
    ).where(ScenarioRun.scenario_id.in_(ids))
    if observation_date is not None:
        query = query.where(ScenarioRun.observation_date == observation_date)
    ranked = query.subquery()

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

    Принадлежность к сетке — поле, а не тег: по тегу отбор пришлось бы делать
    в Python, потому что ``JSONB.contains`` работает только в PostgreSQL.

    Мягко удаленные не отсеиваются по ``deleted_at``: состав сетки меняется —
    пятидневные брони проживания перестали наблюдаться на каждую дату, — и
    снятый с наблюдения сценарий все еще несет накопленные наблюдения, которые
    витрина вправе показывать. Отбор по составу делают вызывающие функции.
    """
    return list(
        session.scalars(
            select(TravelScenario)
            .where(TravelScenario.is_showcase_grid.is_(True))
            .where(TravelScenario.departure_date >= departure_from)
            .where(TravelScenario.departure_date <= departure_to)
            .order_by(TravelScenario.departure_date)
        ).all()
    )


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
    *,
    observation_date: date | None = None,
) -> list[_Observation]:
    scenarios = list(scenarios)
    runs = _latest_runs(session, scenarios, observation_date=observation_date)
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


def _is_stay(scenario: TravelScenario) -> bool:
    """Сценарий наблюдает только проживание."""
    return scenario.transport_type is None and scenario.accommodation_type is not None


def _price_block(
    *,
    median: Any,
    minimum: Any,
    offers: int,
    sources: int,
    run_id: Any,
    per_unit: float | None = None,
) -> dict[str, Any] | None:
    """Цена вместе с тем, на чем она стоит.

    ``None`` возвращается там, где медианы нет: цифра без нее не показывается,
    а причина отсутствия объясняется отдельно — пустая клетка без объяснения
    читается как «поездка бесплатна».
    """
    value = _money(median)
    if value is None:
        return None
    return {
        "median": value,
        # Самое дешевое допущенное предложение — уже после фильтров и отсечения
        # выбросов, поэтому льготные места и распроданные вагоны его не
        # занижают: они отсекаются профилем раньше.
        "min": _money(minimum),
        "offers": int(offers or 0),
        "sources": int(sources or 0),
        "run_id": str(run_id) if run_id else None,
        **({"per_unit": per_unit} if per_unit is not None else {}),
    }


def _two_leg_price(
    outbound: _Observation | None, inbound: _Observation | None
) -> dict[str, Any] | None:
    """Цена поездки двумя билетами: плечо «туда» плюс плечо «обратно».

    Складывается на любой интервал — тем и отличается от кругового тарифа,
    которого на нештатных датах не существует вовсе. Сумма двух плеч, как
    правило, дороже кругового: разница и есть цена гибкости дат.

    Требуются оба плеча. Одно плечо — это половина поездки, и показывать ее
    рядом с полной ценой значило бы сравнивать разное.
    """
    if outbound is None or inbound is None:
        return None
    parts = [
        (_money(item.run.transport_median), _money(item.run.transport_min), item.run)
        for item in (outbound, inbound)
    ]
    if any(median is None for median, _, _ in parts):
        return None

    minimums = [minimum for _, minimum, _ in parts]
    return {
        "median": sum(median for median, _, _ in parts),
        # Сумма минимумов по плечам: нижняя граница, а не наблюдавшаяся пара.
        "min": sum(minimums) if all(value is not None for value in minimums) else None,
        "offers": sum(int(run.transport_offer_count or 0) for _, _, run in parts),
        "sources": max(int(run.transport_source_count or 0) for _, _, run in parts),
        # Разбор открывается по плечу «туда»: у поездки двумя билетами нет
        # одного расчета, а показать надо тот, с которого она начинается.
        "run_id": str(outbound.run.id),
        "legs": [
            {
                "direction": direction,
                "date": item.scenario.departure_date.isoformat(),
                "median": median,
                "min": minimum,
                "run_id": str(item.run.id),
            }
            for direction, item, (median, minimum, _) in (
                ("OUTBOUND", outbound, parts[0]),
                ("INBOUND", inbound, parts[1]),
            )
        ],
    }


def _stay_per_night(run: ScenarioRun, scenario: TravelScenario) -> tuple[float | None, float | None]:
    """Цена ночи и минимум за ночь по наблюдению проживания.

    Наблюдения бывают двух длительностей: однодневные (основной ряд) и
    пятидневные (контрольные). Приведение к ночи делает их сопоставимыми, но
    не одинаковыми: цена ночи в пятидневной броне усреднена по будням и
    выходным сразу, и именно поэтому график строится по однодневным.
    """
    nights = max(1, int(scenario.nights or 1))
    median = _money(run.hotel_median)
    minimum = _money(run.hotel_min)
    return (
        median / nights if median is not None else None,
        minimum / nights if minimum is not None else None,
    )


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
    observation_date: date | None = None,
) -> dict[str, Any]:
    """Варианты отдыха из выбранного города на выбранные даты.

    Транспорт и проживание наблюдаются разными сценариями и складываются здесь:
    проживание от города отправления не зависит, и наблюдать его на каждый
    маршрут значило бы собирать одно и то же по четыре раза.

    Длительность поездки задается пользователем и с наблюдаемой совпадать не
    обязана. Проживание в этом случае считается как медиана ночи на число
    ночей — человек, впрочем, платит за бронь, а не за сумму ночей, поэтому
    цифра помечается оценкой. Транспорт наблюдается только на каноническую
    длительность, и на других датах его в сетке просто нет: это честно
    показывается пустой клеткой, а не подставленной цифрой с соседней даты.
    """
    names = _city_names(session)
    nights = (return_date - departure_date).days
    # Берутся обе даты: на дату отправления лежат плечо «туда» и проживание,
    # на дату возврата — встречное плечо «обратно», из которого складывается
    # поездка двумя билетами.
    scenarios = [
        item
        for item in _grid_scenarios(
            session, departure_from=departure_date, departure_to=return_date
        )
        if item.departure_date in (departure_date, return_date)
    ]
    observed = _observations(session, scenarios, observation_date=observation_date)

    transport: dict[str, _Observation] = {}
    #: Плечо «туда»: маршрут из своего города на дату отправления.
    outbound_leg: dict[str, _Observation] = {}
    #: Плечо «обратно»: встречный маршрут на дату возвращения.
    inbound_leg: dict[str, _Observation] = {}
    accommodation: dict[str, _Observation] = {}
    for item in observed:
        scenario = item.scenario
        if scenario.transport_type == transport_type.value:
            origin_code = scenario.origin_city.code
            destination_code = scenario.destination_city.code
            if scenario.is_one_way:
                if origin_code == origin and scenario.departure_date == departure_date:
                    outbound_leg[destination_code] = item
                elif destination_code == origin and scenario.departure_date == return_date:
                    # Встречный маршрут: обратно едут из города назначения.
                    inbound_leg[origin_code] = item
            elif origin_code == origin and scenario.return_date == return_date:
                # Круговой тариф продается на конкретную пару дат: на другой
                # длительности такого наблюдения нет вовсе, и подставлять
                # соседнее значило бы выдать чужую цену за эту поездку.
                transport[destination_code] = item
        elif (
            _is_stay(scenario)
            and scenario.stars == stars.value
            and scenario.departure_date == departure_date
        ):
            # Из двух длительностей берется однодневная: она основной ряд, а
            # пятидневная контрольная и наблюдается лишь на трех датах.
            current = accommodation.get(scenario.destination_city.code)
            if current is None or scenario.nights < current.scenario.nights:
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
        once = on_demand.get(code)
        from_on_demand = False

        transport_price = (
            _price_block(
                median=transport_run.run.transport_median,
                minimum=transport_run.run.transport_min,
                offers=transport_run.run.transport_offer_count,
                sources=transport_run.run.transport_source_count,
                run_id=transport_run.run.id,
            )
            if transport_run
            else None
        )
        # Поездка двумя билетами: плечо «туда» плюс встречное плечо «обратно».
        # Складывается на любой интервал — тем и отличается от кругового
        # тарифа, которого на нештатных датах просто не существует.
        legs_price = _two_leg_price(outbound_leg.get(code), inbound_leg.get(code))

        stay_price = None
        stay_estimated = False
        if stay_run is not None:
            per_night, min_per_night = _stay_per_night(stay_run.run, stay_run.scenario)
            if per_night is not None:
                stay_estimated = stay_run.scenario.nights != nights
                stay_price = {
                    "median": per_night * nights,
                    "min": min_per_night * nights if min_per_night is not None else None,
                    "offers": int(stay_run.run.hotel_offer_count or 0),
                    "sources": int(stay_run.run.hotel_source_count or 0),
                    "run_id": str(stay_run.run.id),
                    "per_unit": per_night,
                    # Оценка, а не наблюдение: человек платит за бронь, а не за
                    # сумму ночей, и короткие брони отели продают дороже.
                    "estimated": stay_estimated,
                    "observed_nights": int(stay_run.scenario.nights or 1),
                }

        # Наблюдение сетки в приоритете: разовый расчет дополняет его, а не
        # подменяет. Иначе один пересчет менял бы цифру уже показанного ряда.
        if once is not None:
            if transport_price is None and legs_price is None:
                # Разовый расчет делается круговым: он про конкретную поездку.
                transport_price = _price_block(
                    median=once.run.transport_median,
                    minimum=once.run.transport_min,
                    offers=once.run.transport_offer_count,
                    sources=once.run.transport_source_count,
                    run_id=once.run.id,
                )
                from_on_demand = from_on_demand or transport_price is not None
            if stay_price is None:
                stay_price = _price_block(
                    median=once.run.hotel_median,
                    minimum=once.run.hotel_min,
                    offers=once.run.hotel_offer_count,
                    sources=once.run.hotel_source_count,
                    run_id=once.run.id,
                )
                if stay_price is not None:
                    stay_price["estimated"] = False
                    stay_price["observed_nights"] = nights
                    from_on_demand = True

        # Итог считается по той цене проезда, которая на эти даты вообще
        # существует. Круговой тариф точнее — это цена реальной покупки, — но
        # на нештатной длительности его нет, и тогда поездка складывается из
        # двух билетов. Что именно взято, видно по ``transport_basis``.
        chosen_transport = transport_price or legs_price
        transport_basis = (
            "ROUND_TRIP" if transport_price else ("TWO_LEGS" if legs_price else None)
        )

        total = None
        if chosen_transport and stay_price:
            total = {
                "median": chosen_transport["median"] + stay_price["median"],
                # Сумма минимумов — нижняя граница, а не предложение: дешевый
                # рейс бывает в неудобное время, а дешевый отель к тому моменту
                # разберут. Подписывать ее следует «не дешевле чем».
                "min": (
                    chosen_transport["min"] + stay_price["min"]
                    if chosen_transport["min"] is not None and stay_price["min"] is not None
                    else None
                ),
                "estimated": stay_estimated or transport_basis == "TWO_LEGS",
            }

        items.append(
            {
                "destination_code": code,
                "destination_name": names.get(code, code),
                "transport": chosen_transport,
                # Обе цены проезда рядом: «одним билетом» и «двумя билетами».
                # Совпали даты с наблюдаемой длительностью — заполнены обе, и
                # видна цена гибкости дат; даты произвольные — кругового
                # тарифа на них не наблюдалось, и клетка пуста, что честно
                # читается как «так не продают на эти даты».
                "transport_round_trip": transport_price,
                "transport_two_legs": legs_price,
                "transport_basis": transport_basis,
                "accommodation": stay_price,
                "total": total,
                # Разовый расчет — это один снимок по запросу, а не наблюдение
                # сетки. Интерфейс должен иметь возможность это показать.
                "on_demand": from_on_demand,
                # Показываем то, чего не хватило: пустая строка без объяснения
                # читается как «поездка бесплатна».
                "missing": [
                    name
                    for name, value in (
                        ("transport", chosen_transport),
                        ("accommodation", stay_price),
                    )
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
        "observation_date": observation_date.isoformat() if observation_date else None,
        "nights": nights,
        "transport_type": transport_type.value,
        "stars": stars.value,
        "travelers": SHOWCASE_ADULTS,
        "items": items,
        "available": any(item["total"] is not None for item in items),
        "grid_nights": CANONICAL_NIGHTS,
        "stay_nights_observed": STAY_NIGHTS,
    }


# --------------------------------------------------------------------------- #
# Блок 2. Кривые по датам отправления
# --------------------------------------------------------------------------- #


def _one_way_prices(
    session: Session, snapshot_ids: Sequence[Any]
) -> dict[Any, tuple[float, float]]:
    """Медиана и минимум цены поездки в одну сторону по каждому снимку.

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

    prices: dict[Any, tuple[float, float]] = {}
    for snapshot_id, values in buckets.items():
        ordered = sorted(values)
        median = percentile(ordered, 0.5, "LINEAR")
        if median is None:
            continue
        # Цена за место, а состав тургруппы задан методикой витрины.
        prices[snapshot_id] = (
            float(median) * SHOWCASE_ADULTS,
            float(ordered[0]) * SHOWCASE_ADULTS,
        )
    return prices


def transport_curve(
    session: Session,
    *,
    origin: str,
    transport_type: TransportType = TransportType.RAIL,
    days: int = HORIZON_DAYS,
    today: date | None = None,
    observation_date: date | None = None,
) -> dict[str, Any]:
    """Цена проезда по датам отправления.

    Отвечает на вопрос «сколько стоит выехать сегодня, завтра, через неделю» —
    это глубина бронирования, а не движение цены во времени: вся кривая
    собирается из одного цикла наблюдений.

    Единица измерения зависит от вида проезда, и это не небрежность, а свойство
    рынка. У ЖД цена плеча приходит от источника своим полем, поэтому кривая
    показывает поездку в одну сторону. Авиабилет продается круговым тарифом
    одним числом, разделить его на плечи нельзя — деление было бы выдумкой,
    поэтому кривая показывает поездку туда-обратно. Интерфейс обязан назвать
    это: возвращаемое ``direction`` для того и нужно.
    """
    today = today or utcnow().date()
    horizon = today + timedelta(days=days)
    names = _city_names(session)

    # Кривая строится по одностороннему наблюдению там, где оно есть: у ЖД
    # плечо наблюдается прямо, у авиа — вторым рядом рядом с круговым.
    # Круговое наблюдение отвечает на другой вопрос: «сколько стоит поездка на
    # эти даты», а кривая спрашивает «когда дешевле выехать».
    scenarios = [
        item
        for item in _grid_scenarios(session, departure_from=today, departure_to=horizon)
        if item.transport_type == transport_type.value
        and item.origin_city.code == origin
        and item.is_one_way
    ]
    fallback_to_round_trip = not scenarios
    if fallback_to_round_trip:
        # Односторонних наблюдений еще нет — показываем круговые, честно
        # называя это в ``direction``. Пустой график был бы хуже: он читается
        # как отсутствие рынка, а не как отсутствие нового ряда наблюдений.
        scenarios = [
            item
            for item in _grid_scenarios(session, departure_from=today, departure_to=horizon)
            if item.transport_type == transport_type.value
            and item.origin_city.code == origin
        ]

    observed = _observations(session, scenarios, observation_date=observation_date)
    is_one_way = not fallback_to_round_trip
    # У ЖД цена плеча лежит у предложения своим полем и точнее итога расчета:
    # расчет хранит стоимость наблюдения целиком, а нам нужна одна поездка.
    one_way = (
        _one_way_prices(
            session,
            [item.run.market_snapshot_id for item in observed if item.run.market_snapshot_id],
        )
        if transport_type == TransportType.RAIL
        else {}
    )

    series: dict[str, list[dict[str, Any]]] = {
        code: [] for code in SHOWCASE_CITIES if code != origin
    }
    for item in observed:
        code = item.scenario.destination_city.code
        if code not in series:
            continue
        run = item.run
        pair = one_way.get(run.market_snapshot_id)
        if pair is not None:
            median, minimum = pair
        else:
            median = _money(run.transport_median)
            minimum = _money(run.transport_min)
            if median is None:
                continue
        series[code].append(
            {
                "departure_date": item.scenario.departure_date.isoformat(),
                "price": median,
                "min": minimum,
                "offers": int(run.transport_offer_count or 0),
                "sources": int(run.transport_source_count or 0),
                # Без идентификатора расчета от точки графика некуда перейти, и
                # разбор цены остается недостижим оттуда, где вопрос возникает.
                "run_id": str(run.id),
            }
        )

    return {
        "origin_code": origin,
        "origin_name": names.get(origin, origin),
        "transport_type": transport_type.value,
        "direction": "ONE_WAY" if is_one_way else "ROUND_TRIP",
        "travelers": SHOWCASE_ADULTS,
        "horizon_days": days,
        "observation_date": observation_date.isoformat() if observation_date else None,
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
    origin: str | None = None,
    stars: StarsFilter = CURVE_STARS,
    days: int = HORIZON_DAYS,
    today: date | None = None,
    observation_date: date | None = None,
) -> dict[str, Any]:
    """Медиана проживания за одну ночь по датам заезда.

    Показываются все пять городов независимо от того, откуда собрался ехать
    пользователь: график отвечает на вопрос «когда в городе дорого», а не
    «куда поехать», и свой город в нем такой же ответ, как чужой.

    Одна ночь, а не пять. Цена ночи внутри пятидневной брони усреднена по
    будням и выходным сразу — заезд в среду означает, что в брони среда,
    четверг, пятница, суббота и воскресенье, — и на графике по датам заезда это
    размазывает недельную волну по пяти соседним точкам. Однодневная бронь дает
    одну точку на одну ночь и один день недели.
    """
    today = today or utcnow().date()
    horizon = today + timedelta(days=days)
    names = _city_names(session)

    scenarios = [
        item
        for item in _grid_scenarios(session, departure_from=today, departure_to=horizon)
        if _is_stay(item) and item.stars == stars.value and item.nights == STAY_NIGHTS
    ]
    observed = _observations(session, scenarios, observation_date=observation_date)

    series: dict[str, list[dict[str, Any]]] = {code: [] for code in SHOWCASE_CITIES}
    for item in observed:
        code = item.scenario.destination_city.code
        if code not in series:
            continue
        median, minimum = _stay_per_night(item.run, item.scenario)
        if median is None:
            continue
        series[code].append(
            {
                "check_in": item.scenario.departure_date.isoformat(),
                "price": median,
                "min": minimum,
                "offers": int(item.run.hotel_offer_count or 0),
                "sources": int(item.run.hotel_source_count or 0),
                "run_id": str(item.run.id),
            }
        )

    return {
        "origin_code": origin,
        "stars": stars.value,
        "nights": STAY_NIGHTS,
        "travelers": SHOWCASE_ADULTS,
        "horizon_days": days,
        "observation_date": observation_date.isoformat() if observation_date else None,
        "series": [
            {
                "city_code": code,
                "city_name": names.get(code, code),
                "points": sorted(points, key=lambda point: point["check_in"]),
            }
            for code, points in series.items()
        ],
    }


# --------------------------------------------------------------------------- #
# Даты наблюдения
# --------------------------------------------------------------------------- #


def observation_dates(session: Session, *, limit: int = 30) -> dict[str, Any]:
    """Даты, на которые есть наблюдения сетки.

    Нужны переключателю даты построения графика: без списка пустой график
    неотличим от отсутствия наблюдений, и пользователь не может понять, выбрал
    он день без данных или сломалась витрина.
    """
    grid = select(TravelScenario.id).where(TravelScenario.is_showcase_grid.is_(True))
    rows = session.execute(
        select(ScenarioRun.observation_date, func.count())
        .where(ScenarioRun.scenario_id.in_(grid))
        .group_by(ScenarioRun.observation_date)
        .order_by(ScenarioRun.observation_date.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {"observation_date": day.isoformat(), "runs": int(count)} for day, count in rows
        ],
        "latest": rows[0][0].isoformat() if rows else None,
    }


# --------------------------------------------------------------------------- #
# Расхождение оценки с реальной бронью
# --------------------------------------------------------------------------- #


def stay_estimate_accuracy(
    session: Session, *, days: int = HORIZON_DAYS, today: date | None = None
) -> dict[str, Any]:
    """Насколько «ночь × число ночей» расходится с ценой пятидневной брони.

    Витрина считает стоимость проживания из цены одной ночи. Отели продают
    короткие брони дороже, поэтому такая оценка систематически смещена, и
    величину смещения нельзя предполагать — ее наблюдают контрольные
    пятидневные брони. Это постоянный показатель точности, а не разовый замер.
    """
    today = today or utcnow().date()
    horizon = today + timedelta(days=days)
    scenarios = [
        item
        for item in _grid_scenarios(session, departure_from=today, departure_to=horizon)
        if _is_stay(item)
    ]
    observed = _observations(session, scenarios)

    single: dict[tuple[str, str, str], float] = {}
    control: dict[tuple[str, str, str], tuple[float, int]] = {}
    for item in observed:
        key = (
            item.scenario.destination_city.code,
            item.scenario.stars,
            item.scenario.departure_date.isoformat(),
        )
        median = _money(item.run.hotel_median)
        if median is None:
            continue
        nights = max(1, int(item.scenario.nights or 1))
        if nights == STAY_NIGHTS:
            single[key] = median
        else:
            control[key] = (median, nights)

    rows: list[dict[str, Any]] = []
    for key, (booking_price, nights) in sorted(control.items()):
        per_night = single.get(key)
        if per_night is None:
            continue
        estimate = per_night * nights
        rows.append(
            {
                "city_code": key[0],
                "stars": key[1],
                "check_in": key[2],
                "nights": nights,
                "estimate": estimate,
                "observed_booking": booking_price,
                "deviation": round((estimate - booking_price) / booking_price, 4)
                if booking_price
                else None,
            }
        )

    deviations = [row["deviation"] for row in rows if row["deviation"] is not None]
    return {
        "pairs": len(rows),
        "median_deviation": (
            float(percentile(sorted(to_decimal(item) for item in deviations), 0.5, "LINEAR"))
            if deviations
            else None
        ),
        "rows": rows,
    }
