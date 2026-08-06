"""Витрина вариантов отдыха по пяти ключевым городам.

Отдает то, что уже наблюдено скользящей сеткой. Обращений к источникам здесь
нет: 20 маршрутов на 30 дат — это минуты ожидания и неповторяемая цифра при
каждом обновлении страницы.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from tco.api.deps import SessionDep, ViewerDep
from tco.core.enums import StarsFilter, TransportType
from tco.core.errors import ValidationError
from tco.services import showcase as service
from tco.services.observation_grid import HORIZON_DAYS, SHOWCASE_CITIES
from tco.version import METRIC_DISCLAIMER_RU

router = APIRouter(prefix="/showcase", tags=["showcase"])

CityCode = Annotated[
    str,
    Query(description=f"Код города витрины: {', '.join(SHOWCASE_CITIES)}"),
]


def _require_showcase_city(code: str) -> str:
    if code not in SHOWCASE_CITIES:
        raise ValidationError(
            f"Город {code} не входит в витрину. Доступны: {', '.join(SHOWCASE_CITIES)}"
        )
    return code


@router.get("/cities", summary="Города витрины")
def showcase_cities(session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    from sqlalchemy import select

    from tco.db.models.reference import City

    rows = session.scalars(select(City).where(City.code.in_(SHOWCASE_CITIES))).all()
    order = {code: index for index, code in enumerate(SHOWCASE_CITIES)}
    return {
        "items": sorted(
            ({"code": city.code, "name": city.name} for city in rows),
            key=lambda item: order.get(item["code"], 99),
        ),
        "horizon_days": HORIZON_DAYS,
    }


@router.get("/options", summary="Варианты отдыха на выбранные даты")
def showcase_options(
    session: SessionDep,
    _: ViewerDep,
    origin: CityCode,
    departure_date: Annotated[date, Query(description="Дата отправления")],
    return_date: Annotated[date, Query(description="Дата возвращения")],
    transport_type: TransportType = TransportType.RAIL,
    stars: StarsFilter = StarsFilter.S3,
) -> dict[str, Any]:
    _require_showcase_city(origin)
    if return_date <= departure_date:
        raise ValidationError("Дата возвращения должна быть позже даты отправления")

    payload = service.options(
        session,
        origin=origin,
        departure_date=departure_date,
        return_date=return_date,
        transport_type=transport_type,
        stars=stars,
    )
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload


@router.get("/transport-curve", summary="Цена проезда по датам отправления")
def showcase_transport_curve(
    session: SessionDep,
    _: ViewerDep,
    origin: CityCode,
    transport_type: Annotated[
        TransportType, Query(description="Вид проезда")
    ] = TransportType.RAIL,
    days: Annotated[int, Query(ge=1, le=180, description="Горизонт в днях")] = HORIZON_DAYS,
) -> dict[str, Any]:
    _require_showcase_city(origin)
    payload = service.transport_curve(
        session, origin=origin, transport_type=transport_type, days=days
    )
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload


@router.get("/accommodation-curve", summary="Медиана проживания по датам заезда")
def showcase_accommodation_curve(
    session: SessionDep,
    _: ViewerDep,
    stars: StarsFilter = StarsFilter.S3,
    days: Annotated[int, Query(ge=1, le=180, description="Горизонт в днях")] = HORIZON_DAYS,
) -> dict[str, Any]:
    payload = service.accommodation_curve(session, stars=stars, days=days)
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload
