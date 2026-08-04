"""Выборка предложений и сопоставление источников между собой.

Логика вынесена из роутеров, чтобы ``/market-snapshots/{id}/offers`` и
``/scenario-runs/{id}/offers`` не разъезжались, а правило «межисточниковая
группа» совпадало с тем, что применяет движок отбора
(``tco.engine.selection.link_equivalence_groups``).
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tco.api.deps import Pagination
from tco.api.serializers import offer as offer_payload
from tco.core.enums import OfferType, ValidityStatus
from tco.db.models.offer import Offer
from tco.db.models.snapshot import MarketSnapshot

#: Верхняя граница на число групп в сравнении. Снимок ограничен одним
#: маршрутом и датой, поэтому на практике групп единицы; предел — страховка.
MAX_COMPARISON_GROUPS = 200


def unavailable(reason: str, page: Pagination | None = None, **extra: Any) -> dict[str, Any]:
    """Ответ, когда предложений нет по объяснимой причине, а не из-за ошибки.

    Возвращается с кодом 200: «данные вышли за срок хранения» — это не сбой,
    и интерфейс должен показать пояснение, а не красную ошибку.
    """
    payload: dict[str, Any] = {
        "offers_available": False,
        "reason": reason,
        "items": [],
        "groups": [],
        "sources": [],
        "truncated": False,
        "summary": {
            "group_count": 0,
            "cross_source_group_count": 0,
            "single_source_group_count": 0,
            "median_delta_ratio": None,
            "max_delta_ratio": None,
        },
        **extra,
    }
    if page is not None:
        payload["meta"] = {
            "page": page.page,
            "page_size": page.page_size,
            "total": 0,
            "total_pages": 0,
        }
    return payload


def paged_offers(
    session: Session,
    snapshot: MarketSnapshot,
    page: Pagination,
    *,
    offer_type: OfferType | None = None,
    source_code: str | None = None,
    valid_only: bool = False,
    exclude_outliers: bool = False,
) -> dict[str, Any]:
    """Постраничная выборка предложений снимка.

    Выбросы и невалидные записи сохраняются и помечаются — они видны в
    диагностике, но не участвуют в агрегированной оценке.
    """
    stmt = select(Offer).where(Offer.market_snapshot_id == snapshot.id)
    if offer_type:
        stmt = stmt.where(Offer.offer_type == offer_type.value)
    if source_code:
        stmt = stmt.where(Offer.source_code == source_code)
    if valid_only:
        stmt = stmt.where(Offer.validity_status == ValidityStatus.VALID.value)
    if exclude_outliers:
        stmt = stmt.where(Offer.is_outlier.is_(False))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Offer.total_price, Offer.offer_type).offset(page.offset).limit(page.limit)
    ).all()

    return {
        "offers_available": True,
        "snapshot_id": str(snapshot.id),
        "items": [offer_payload(row) for row in rows],
        "meta": {
            "page": page.page,
            "page_size": page.page_size,
            "total": total,
            "total_pages": (total + page.page_size - 1) // page.page_size,
        },
    }


# --------------------------------------------------------------------------- #
# Сравнение источников по ЖД
# --------------------------------------------------------------------------- #


def _group_key(item: Offer) -> tuple[str, str | None]:
    """Ключ группировки поездов.

    Основа — ``equivalence_group_id``, который нормализатор строит из номера
    поезда, дат и класса вагона, намеренно исключая источник (см.
    ``tco.normalization.normalizer``). Класс вагона добавлен в ключ отдельно:
    в payload эквивалентности он берется из распознанного ``RailClass``, и
    нераспознанный тип схлопнул бы плацкарт с купе в одну группу.
    """
    rail = item.rail
    car_type = rail.car_type if rail is not None else None
    if item.equivalence_group_id is not None:
        return (str(item.equivalence_group_id), car_type)
    # Без ключа эквивалентности предложение остается отдельной группой,
    # иначе оно молча выпало бы из сравнения.
    return (f"__ungrouped__:{item.id}", car_type)


def _cheaper(candidate: Offer, current: Offer) -> bool:
    if candidate.total_price is None:
        return False
    if current.total_price is None:
        return True
    return candidate.total_price < current.total_price


def _price_entry(item: Offer) -> dict[str, Any]:
    rail = item.rail
    return {
        "offer_id": str(item.id),
        "total_price": float(item.total_price) if item.total_price is not None else None,
        "price_per_place_outbound": float(rail.price_per_place_outbound)
        if rail is not None and rail.price_per_place_outbound is not None
        else None,
        "price_per_place_inbound": float(rail.price_per_place_inbound)
        if rail is not None and rail.price_per_place_inbound is not None
        else None,
        "available_places_outbound": rail.available_places_outbound if rail is not None else None,
        "available_places_inbound": rail.available_places_inbound if rail is not None else None,
        "exclusion_reason": item.exclusion_reason,
        "is_outlier": item.is_outlier,
        "deeplink": item.deeplink,
    }


def _describe(members: list[Offer]) -> dict[str, Any]:
    """Описание поезда берется у первого члена группы.

    Расхождение расписания между источниками не скрывается, а выносится
    отдельным флагом — для бизнеса это понятный сигнал качества данных.
    """
    lead = members[0]
    rail = lead.rail
    departures = {
        m.rail.outbound_departure_at
        for m in members
        if m.rail is not None and m.rail.outbound_departure_at is not None
    }
    return {
        "outbound_train_number": rail.outbound_train_number if rail else None,
        "inbound_train_number": rail.inbound_train_number if rail else None,
        "outbound_departure_at": rail.outbound_departure_at.isoformat()
        if rail is not None and rail.outbound_departure_at is not None
        else None,
        "outbound_arrival_at": rail.outbound_arrival_at.isoformat()
        if rail is not None and rail.outbound_arrival_at is not None
        else None,
        "inbound_departure_at": rail.inbound_departure_at.isoformat()
        if rail is not None and rail.inbound_departure_at is not None
        else None,
        "inbound_arrival_at": rail.inbound_arrival_at.isoformat()
        if rail is not None and rail.inbound_arrival_at is not None
        else None,
        "outbound_duration_minutes": rail.outbound_duration_minutes if rail else None,
        "inbound_duration_minutes": rail.inbound_duration_minutes if rail else None,
        "car_type": rail.car_type if rail else None,
        "car_type_raw": rail.car_type_raw if rail else None,
        "origin_station_name": rail.origin_station_name if rail else None,
        "destination_station_name": rail.destination_station_name if rail else None,
        "carriers": list(rail.carriers or []) if rail else [],
        "sources_disagree_on_schedule": len(departures) > 1,
    }


def build_rail_groups(
    rows: list[Offer],
    *,
    cross_source_only: bool = False,
) -> dict[str, Any]:
    """Чистая группировка ЖД-предложений — без обращения к БД.

    Вынесена отдельно от выборки, чтобы правило группировки можно было
    проверить на заданном наборе предложений.
    """
    buckets: dict[tuple[str, str | None], list[Offer]] = {}
    for item in rows:
        buckets.setdefault(_group_key(item), []).append(item)

    # Группа с одним equivalence_group_id, но разными типами вагона, — признак
    # нераспознанного car_type у одного из источников.
    ambiguous_ids = {
        key[0]
        for key in buckets
        if sum(1 for other in buckets if other[0] == key[0]) > 1
        and not key[0].startswith("__ungrouped__")
    }

    groups: list[dict[str, Any]] = []
    for (group_id, _car_type), members in buckets.items():
        # Источник может отдать несколько тарифов на один поезд; для сравнения
        # цен берется самый дешевый, иначе выбор был бы произвольным.
        best: dict[str, Offer] = {}
        for m in members:
            current = best.get(m.source_code)
            if current is None or _cheaper(m, current):
                best[m.source_code] = m
        prices = {code: _price_entry(m) for code, m in best.items()}
        values = [m.total_price for m in best.values() if m.total_price is not None]
        source_count = len(best)

        if cross_source_only and source_count < 2:
            continue

        min_price: Decimal | None = min(values) if values else None
        max_price: Decimal | None = max(values) if values else None
        delta = max_price - min_price if min_price is not None and max_price is not None else None
        cheapest = min(
            (m for m in best.values() if m.total_price is not None),
            key=lambda m: m.total_price,
            default=None,
        )

        groups.append(
            {
                "equivalence_group_id": None if group_id.startswith("__ungrouped__") else group_id,
                **_describe(members),
                "prices": prices,
                "min_price": float(min_price) if min_price is not None else None,
                "max_price": float(max_price) if max_price is not None else None,
                "delta_absolute": float(delta) if delta is not None else None,
                "delta_ratio": float(delta / min_price)
                if delta is not None and min_price is not None and min_price > 0
                else None,
                "cheapest_source": cheapest.source_code if cheapest is not None else None,
                "source_count": source_count,
                "car_type_ambiguous": group_id in ambiguous_ids,
            }
        )

    groups.sort(key=lambda g: (g["min_price"] is None, g["min_price"]))
    truncated = len(groups) > MAX_COMPARISON_GROUPS
    if truncated:
        groups = groups[:MAX_COMPARISON_GROUPS]

    cross = [g for g in groups if g["source_count"] > 1]
    ratios = [g["delta_ratio"] for g in cross if g["delta_ratio"] is not None]

    return {
        "sources": sorted({item.source_code for item in rows}),
        "groups": groups,
        "truncated": truncated,
        "summary": {
            "group_count": len(groups),
            "cross_source_group_count": len(cross),
            "single_source_group_count": len(groups) - len(cross),
            "median_delta_ratio": statistics.median(ratios) if ratios else None,
            "max_delta_ratio": max(ratios) if ratios else None,
        },
    }


def rail_comparison(
    session: Session,
    snapshot: MarketSnapshot,
    *,
    cross_source_only: bool = False,
    include_excluded: bool = False,
) -> dict[str, Any]:
    """Сопоставление ЖД-предложений разных источников по одним и тем же поездам.

    Считается на сервере, а не на клиенте: группировать нужно по всем
    предложениям снимка сразу (постраничная выдача рвала бы группы), правило
    межисточниковой группы должно совпадать с движком, а разница цен — это
    вычитание ``Decimal``.
    """
    stmt = select(Offer).where(
        Offer.market_snapshot_id == snapshot.id,
        Offer.offer_type == OfferType.RAIL.value,
    )
    if not include_excluded:
        stmt = stmt.where(
            Offer.validity_status == ValidityStatus.VALID.value,
            Offer.is_duplicate.is_(False),
        )

    rows = list(session.scalars(stmt.order_by(Offer.total_price)).all())

    return {
        "offers_available": True,
        "snapshot_id": str(snapshot.id),
        **build_rail_groups(rows, cross_source_only=cross_source_only),
    }


__all__ = [
    "MAX_COMPARISON_GROUPS",
    "build_rail_groups",
    "paged_offers",
    "rail_comparison",
    "unavailable",
]
