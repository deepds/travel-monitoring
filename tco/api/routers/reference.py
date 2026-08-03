"""Справочники конструктора сценария (DELTA §6.2).

Конструктор управляемый: пользователь выбирает только те значения, которые
платформа поддерживает. Источник истины — перечисления ``tco.core.enums``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from tco.api.deps import SessionDep, SettingsDep, ViewerDep
from tco.core.enums import (
    SELECTABLE_ACCOMMODATION_TYPES,
    SELECTABLE_MEAL_TYPES,
    STARRED_ACCOMMODATION_TYPES,
    AccommodationType,
    CancellationFilter,
    FlightFareType,
    RailClass,
    StarsFilter,
    TransportType,
)
from tco.core.utils import utcnow
from tco.db.models.reference import City
from tco.services.calculation import resolve_horizon

router = APIRouter(prefix="/reference", tags=["reference"])

#: Человекочитаемые подписи для UI. Держим рядом со справочником, чтобы
#: интерфейс не хранил собственную копию доменных значений.
_LABELS: dict[str, str] = {
    "AVIA": "Авиа",
    "RAIL": "Железная дорога",
    "CHEAPEST": "Самый дешевый",
    "CABIN_BAGGAGE": "С ручной кладью",
    "CHECKED_BAGGAGE": "С багажом",
    "RESERVED_SEAT": "Плацкарт",
    "COMPARTMENT": "Купе",
    "HOTEL": "Гостиница",
    "APARTMENT": "Апартаменты",
    "GUEST_HOUSE": "Гостевой дом",
    "HOSTEL": "Хостел",
    "SANATORIUM": "Санаторий",
    "OTHER": "Иное",
    "ANY": "Любое",
    "NO_MEALS": "Без питания",
    "BREAKFAST": "Завтрак",
    "HALF_BOARD": "Полупансион",
    "FULL_BOARD": "Полный пансион",
    "ALL_INCLUSIVE": "Всё включено",
    "FREE_CANCELLATION": "Бесплатная отмена",
    "UNRATED": "Без звезд",
    "NOT_APPLICABLE": "Неприменимо",
}


def _option(value: str, label: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"value": value, "label": label or _LABELS.get(value, value), **extra}


@router.get("/cities", summary="Поддерживаемые города")
def cities(
    session: SessionDep,
    _: ViewerDep,
    active_only: bool = Query(True, description="Только активные города"),
) -> dict[str, Any]:
    """Города MVP. Каждый может быть и точкой отправления, и назначения."""
    stmt = select(City).order_by(City.sort_order, City.name)
    if active_only:
        stmt = stmt.where(City.is_active.is_(True))

    items = [
        {
            "id": str(city.id),
            "code": city.code,
            "name": city.name,
            "name_en": city.name_en,
            "region": city.region,
            "timezone": city.timezone,
            "supports_avia": city.supports_avia,
            "supports_rail": city.supports_rail,
            "iata_codes": city.iata_codes,
            "is_active": city.is_active,
        }
        for city in session.scalars(stmt).all()
    ]
    return {"items": items, "total": len(items)}


@router.get("/transport-types", summary="Виды транспорта")
def transport_types(_: ViewerDep) -> dict[str, Any]:
    """Авиа и ЖД — альтернативные варианты сценария, они не комбинируются."""
    return {
        "items": [_option(t.value) for t in TransportType],
        "note": "Комбинированный маршрут «туда самолетом, обратно поездом» в MVP не поддерживается.",
    }


@router.get("/fare-types", summary="Тарифные режимы авиа")
def fare_types(_: ViewerDep) -> dict[str, Any]:
    """Предложение с неопределенным багажом допускается только в ``CHEAPEST``."""
    return {
        "items": [
            _option(
                FlightFareType.CHEAPEST.value,
                requires_baggage_classification=False,
            ),
            _option(FlightFareType.CABIN_BAGGAGE.value, requires_baggage_classification=True),
            _option(FlightFareType.CHECKED_BAGGAGE.value, requires_baggage_classification=True),
        ],
        "note": (
            "Если источник не позволяет надежно определить багаж, предложение "
            "участвует только в тарифе CHEAPEST."
        ),
    }


@router.get("/rail-classes", summary="Классы ЖД")
def rail_classes(_: ViewerDep) -> dict[str, Any]:
    """Плацкарт и купе агрегируются раздельно."""
    return {"items": [_option(c.value) for c in RailClass]}


@router.get("/accommodation-types", summary="Типы размещения")
def accommodation_types(_: ViewerDep) -> dict[str, Any]:
    return {
        "items": [
            _option(
                t.value,
                stars_applicable=t in STARRED_ACCOMMODATION_TYPES,
            )
            for t in SELECTABLE_ACCOMMODATION_TYPES
        ],
        "storage_only": [AccommodationType.OTHER.value],
    }


@router.get("/stars", summary="Категории звездности")
def stars(_: ViewerDep) -> dict[str, Any]:
    """``NOT_APPLICABLE`` используется для типов размещения без звезд."""
    return {
        "items": [
            _option(s.value, label=(f"{s.numeric}★" if s.numeric else _LABELS.get(s.value, s.value)))
            for s in StarsFilter
        ],
        "applicable_to": [t.value for t in STARRED_ACCOMMODATION_TYPES],
    }


@router.get("/meal-types", summary="Типы питания")
def meal_types(_: ViewerDep) -> dict[str, Any]:
    """В конструктор выводится сокращенный набор; остальные значения хранятся."""
    return {
        "items": [_option(m.value) for m in SELECTABLE_MEAL_TYPES],
        "note": "Предложение удовлетворяет фильтру, если его питание не хуже запрошенного.",
    }


@router.get("/cancellation-types", summary="Условия отмены")
def cancellation_types(_: ViewerDep) -> dict[str, Any]:
    return {"items": [_option(c.value) for c in CancellationFilter]}


@router.get("/horizon", summary="Доступный горизонт бронирования")
def horizon(session: SessionDep, settings: SettingsDep, _: ViewerDep) -> dict[str, Any]:
    """Горизонт определяется минимально достаточным покрытием источников.

    Требуется хотя бы один пригодный источник транспорта и один — проживания,
    а не строгое пересечение всех источников (SCOPE-R C §7).
    """
    info = resolve_horizon(session, allow_synthetic=settings.sandbox_sources_enabled)
    today = utcnow().date()
    default_max = today + timedelta(days=settings.default_booking_horizon_days)

    def _iso(value: Any) -> str | None:
        return value.isoformat() if value else None

    return {
        "today": today.isoformat(),
        "transport": {
            "sources": info.transport_sources,
            "min_date": _iso(info.transport_min_date),
            "max_date": _iso(info.transport_max_date),
        },
        "accommodation": {
            "sources": info.accommodation_sources,
            "min_date": _iso(info.accommodation_min_date),
            "max_date": _iso(info.accommodation_max_date),
        },
        "default_max_date": default_max.isoformat(),
        "max_booking_horizon_days": settings.default_booking_horizon_days,
    }
