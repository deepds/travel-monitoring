"""Скользящая сетка наблюдений для витрины вариантов отдыха.

Витрина показывает цену по датам отправления на горизонт вперед. Это значит,
что наблюдать нужно не фиксированную дату, а расстояние до нее: сценарий с
жестко заданным 18 сентября каждый день становится ближе, и кривая «поехать
через N дней» из таких наблюдений не собирается.

Отсюда сетка: набор сценариев на каждый день горизонта, который ежедневно
досоздается на дальнем конце и снимается с наблюдения на ближнем. Отдельной
сущности для этого не заводится — сетка живет обычными сценариями, помеченными
тегом, и пользуется всем существующим механизмом сбора и расчета.

Состав на один день горизонта:

* транспорт — 20 направленных маршрутов между пятью городами, отдельно ЖД и
  авиа, туда-обратно, без проживания;
* проживание — пять городов в трех категориях звездности, без транспорта.

Итого 55 сценариев на дату, 1650 на горизонт в 30 дней.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import permutations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    FlightFareType,
    MealType,
    RailClass,
    ScenarioType,
    StarsFilter,
    TransportType,
)
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.services.scenario_lifecycle import soft_delete_scenario
from tco.services.scenarios import DAILY_CADENCE_TAG, ScenarioDraft, create_scenario

#: Пять ключевых городов витрины.
SHOWCASE_CITIES: tuple[str, ...] = ("MOW", "LED", "AER", "KUF", "KZN")

#: Горизонт наблюдения в днях.
HORIZON_DAYS = 30

#: Длительность поездки. Одна на всю сетку: перебирать еще и длительность
#: значило бы умножить объем наблюдений на число вариантов, а витрина
#: сравнивает направления между собой, а не поездки разной длины.
CANONICAL_NIGHTS = 5

#: Категории размещения. Третья звезда — массовый сегмент и основа графиков,
#: четвертая и пятая нужны выбору категории в первом блоке.
SHOWCASE_STARS: tuple[StarsFilter, ...] = (
    StarsFilter.S3,
    StarsFilter.S4,
    StarsFilter.S5,
)

#: Состав тургруппы: один человек. Семьи витрина не собирает.
SHOWCASE_ADULTS = 1

#: Тег, по которому сетка отличается от прочего каталога.
GRID_TAG = "showcase-grid"

#: Профиль расчета витрины.
GRID_PROFILE_CODE = "mass-market"


@dataclass(slots=True)
class GridReport:
    """Что сделала обслуживающая задача."""

    created: int = 0
    existing: int = 0
    retired: int = 0
    horizon_until: date | None = None
    profile_missing: bool = False

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "existing": self.existing,
            "retired": self.retired,
            "horizon_until": self.horizon_until.isoformat() if self.horizon_until else None,
            "profile_missing": self.profile_missing,
        }


def _accommodation_origin(city: str) -> str:
    """Город отправления для сценария, наблюдающего только проживание.

    Проживание от города отправления не зависит, но модель сценария требует
    обе точки маршрута и не допускает их совпадения. Поэтому берется любой
    другой город витрины — на наблюдение он не влияет и нужен только чтобы
    сценарий был корректен. Пара фиксирована, иначе отпечаток сценария менялся
    бы от запуска к запуску и сетка каждый день создавалась бы заново.
    """
    return "LED" if city == "MOW" else "MOW"


def _transport_drafts(origin: str, destination: str, departure: date) -> list[ScenarioDraft]:
    ret = departure + timedelta(days=CANONICAL_NIGHTS)
    common = {
        "origin_city_code": origin,
        "destination_city_code": destination,
        "departure_date": departure,
        "return_date": ret,
        "adults": SHOWCASE_ADULTS,
        "accommodation_type": None,
        "stars": StarsFilter.NOT_APPLICABLE,
        "meal_type": MealType.ANY,
        "cancellation_filter": CancellationFilter.ANY,
        "scenario_type": ScenarioType.MONITORING,
        "tags": [GRID_TAG, DAILY_CADENCE_TAG, "showcase-transport"],
    }
    return [
        ScenarioDraft(
            transport_type=TransportType.RAIL,
            flight_fare_type=None,
            rail_class=RailClass.COMPARTMENT,
            **common,
        ),
        ScenarioDraft(
            transport_type=TransportType.AVIA,
            flight_fare_type=FlightFareType.CHEAPEST,
            rail_class=None,
            **common,
        ),
    ]


def _accommodation_drafts(city: str, check_in: date) -> list[ScenarioDraft]:
    check_out = check_in + timedelta(days=CANONICAL_NIGHTS)
    return [
        ScenarioDraft(
            origin_city_code=_accommodation_origin(city),
            destination_city_code=city,
            departure_date=check_in,
            return_date=check_out,
            adults=SHOWCASE_ADULTS,
            transport_type=None,
            flight_fare_type=None,
            rail_class=None,
            accommodation_type=AccommodationType.HOTEL,
            stars=stars,
            meal_type=MealType.ANY,
            cancellation_filter=CancellationFilter.ANY,
            scenario_type=ScenarioType.MONITORING,
            tags=[GRID_TAG, DAILY_CADENCE_TAG, "showcase-accommodation"],
        )
        for stars in SHOWCASE_STARS
    ]


def grid_drafts(*, today: date, horizon_days: int = HORIZON_DAYS) -> list[ScenarioDraft]:
    """Полный состав сетки на заданный горизонт."""
    drafts: list[ScenarioDraft] = []
    for offset in range(1, horizon_days + 1):
        day = today + timedelta(days=offset)
        for origin, destination in permutations(SHOWCASE_CITIES, 2):
            drafts.extend(_transport_drafts(origin, destination, day))
        for city in SHOWCASE_CITIES:
            drafts.extend(_accommodation_drafts(city, day))
    return drafts


def maintain_grid(
    session: Session,
    *,
    today: date | None = None,
    horizon_days: int = HORIZON_DAYS,
) -> GridReport:
    """Досоздает сетку на горизонт вперед и снимает истекшие даты.

    Повторный запуск в тот же день ничего не меняет: сценарий опознается по
    отпечатку, и ``create_scenario`` возвращает существующий.
    """
    today = today or utcnow().date()
    report = GridReport(horizon_until=today + timedelta(days=horizon_days))

    profile = session.scalars(
        select(CalculationProfile).where(CalculationProfile.code == GRID_PROFILE_CODE)
    ).first()
    # Без своего профиля сетка считалась бы по базовой методике — с другими
    # правилами по классу вагона и возвратности, то есть показывала бы не то,
    # что просили. Лучше не создавать ее вовсе и сказать об этом явно.
    if profile is None:
        report.profile_missing = True
        return report

    for draft in grid_drafts(today=today, horizon_days=horizon_days):
        scenario, created = create_scenario(session, draft, profile=profile, created_by="grid")
        # Признак ставится и существующим: сетка опознается по отпечатку, и
        # сценарий, заведенный до появления поля, иначе остался бы неотмеченным
        # и продолжил бы попадать в агрегаты рынка.
        scenario.is_showcase_grid = True
        if created:
            report.created += 1
        else:
            report.existing += 1

    report.retired = retire_expired(session, today=today)
    return report


def retire_expired(session: Session, *, today: date | None = None) -> int:
    """Снимает с наблюдения сценарии сетки, дата отправления которых прошла."""
    today = today or utcnow().date()
    past = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.is_showcase_grid.is_(True))
        .where(TravelScenario.departure_date < today)
    ).all()

    for scenario in past:
        soft_delete_scenario(scenario)
    return len(past)
