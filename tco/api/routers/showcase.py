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
from tco.services import coverage
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


#: Дата наблюдения: по какому срезу строить картину. Без нее берется последнее
#: наблюдение каждого сценария — это поведение по умолчанию и оно же самое
#: частое; выбор даты нужен, чтобы посмотреть, какой картина была вчера.
ObservationDate = Annotated[
    date | None, Query(description="Дата наблюдения; по умолчанию — последнее")
]


@router.get("/options", summary="Варианты отдыха на выбранные даты")
def showcase_options(
    session: SessionDep,
    _: ViewerDep,
    origin: CityCode,
    departure_date: Annotated[date, Query(description="Дата отправления")],
    return_date: Annotated[date, Query(description="Дата возвращения")],
    transport_type: TransportType = TransportType.RAIL,
    stars: StarsFilter = StarsFilter.S3,
    observation_date: ObservationDate = None,
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
        observation_date=observation_date,
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
    observation_date: ObservationDate = None,
) -> dict[str, Any]:
    _require_showcase_city(origin)
    payload = service.transport_curve(
        session,
        origin=origin,
        transport_type=transport_type,
        days=days,
        observation_date=observation_date,
    )
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload


@router.get("/accommodation-curve", summary="Медиана проживания за ночь по датам заезда")
def showcase_accommodation_curve(
    session: SessionDep,
    _: ViewerDep,
    stars: StarsFilter = StarsFilter.S3,
    origin: Annotated[
        str | None, Query(description="Город отправления: только для подписи")
    ] = None,
    days: Annotated[int, Query(ge=1, le=180, description="Горизонт в днях")] = HORIZON_DAYS,
    observation_date: ObservationDate = None,
) -> dict[str, Any]:
    if origin is not None:
        _require_showcase_city(origin)
    payload = service.accommodation_curve(
        session, origin=origin, stars=stars, days=days, observation_date=observation_date
    )
    payload["disclaimer"] = METRIC_DISCLAIMER_RU
    return payload


@router.get("/observation-dates", summary="Даты, на которые есть наблюдения")
def showcase_observation_dates(
    session: SessionDep,
    _: ViewerDep,
    limit: Annotated[int, Query(ge=1, le=180)] = 30,
) -> dict[str, Any]:
    """Список дат наблюдения для переключателя.

    Без него пустой график неотличим от отсутствия наблюдений: пользователь не
    может понять, выбрал он день без данных или сломалась витрина.
    """
    return service.observation_dates(session, limit=limit)


@router.get("/coverage", summary="Матрица покрытия наблюдений")
def showcase_coverage(
    session: SessionDep,
    _: ViewerDep,
    days: Annotated[int, Query(ge=1, le=180)] = HORIZON_DAYS,
    observation_date: ObservationDate = None,
) -> dict[str, Any]:
    """Где есть цифра, где она на двух предложениях, а где дыра.

    Дыры должны быть видны глазом, а не вычитываться из графика.
    """
    return coverage.coverage_matrix(session, observation_date=observation_date, days=days)


@router.get("/quality", summary="Качество суточного прогона")
def showcase_quality(
    session: SessionDep,
    _: ViewerDep,
    day: Annotated[date | None, Query(description="День прогона")] = None,
) -> dict[str, Any]:
    """Сколько сценариев было в плане, сколько собралось, что помешало.

    Плюс постоянный показатель точности оценки проживания: насколько «ночь ×
    число ночей» расходится с ценой реальной пятидневной брони.
    """
    payload = coverage.daily_run_summary(session, day=day)
    payload["stay_estimate_accuracy"] = service.stay_estimate_accuracy(session)
    return payload
