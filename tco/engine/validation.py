"""Валидация сценария (SCOPE-R P §3).

Невалидный сценарий завершается статусом ``UNSUPPORTED`` до любых внешних
запросов — это одновременно требование методики и защита от лишней нагрузки
на источники.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    FlightFareType,
    MealType,
    RailClass,
    SELECTABLE_ACCOMMODATION_TYPES,
    STARRED_ACCOMMODATION_TYPES,
    StarsFilter,
    TransportType,
)
from tco.core.utils import utcnow

#: Максимальный состав туристов, поддерживаемый MVP.
MAX_ADULTS = 8
MAX_CHILDREN = 4
MAX_CHILD_AGE = 17
#: Максимальная длительность поездки (ночей).
MAX_NIGHTS = 30


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "field": self.field}


@dataclass(slots=True)
class ValidationResult:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add_error(self, code: str, message: str, field_name: str | None = None) -> None:
        self.errors.append(ValidationIssue(code, message, field_name))

    def add_warning(self, code: str, message: str, field_name: str | None = None) -> None:
        self.warnings.append(ValidationIssue(code, message, field_name))

    def as_dict(self) -> dict[str, list[dict[str, str | None]]]:
        return {
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }

    @property
    def summary(self) -> str:
        return "; ".join(issue.message for issue in self.errors)


@dataclass(slots=True)
class ScenarioInput:
    """Параметры сценария в форме, независимой от ORM и API."""

    origin_city_code: str
    destination_city_code: str
    departure_date: date
    return_date: date
    adults: int
    children_ages: tuple[int, ...]
    transport_type: TransportType
    accommodation_type: AccommodationType
    stars: StarsFilter
    meal_type: MealType
    cancellation_filter: CancellationFilter
    flight_fare_type: FlightFareType | None = None
    rail_class: RailClass | None = None


@dataclass(slots=True)
class HorizonInfo:
    """Доступный горизонт, вычисленный по активным источникам (SCOPE-R C §7).

    Общий горизонт определяется не строгим пересечением всех источников,
    а наличием минимально достаточного покрытия: хотя бы один пригодный
    источник транспорта и хотя бы один — проживания.
    """

    transport_min_date: date | None = None
    transport_max_date: date | None = None
    accommodation_min_date: date | None = None
    accommodation_max_date: date | None = None
    transport_sources: list[str] = field(default_factory=list)
    accommodation_sources: list[str] = field(default_factory=list)

    @property
    def has_transport(self) -> bool:
        return bool(self.transport_sources)

    @property
    def has_accommodation(self) -> bool:
        return bool(self.accommodation_sources)

    def covers_transport(self, value: date) -> bool:
        if self.transport_min_date and value < self.transport_min_date:
            return False
        if self.transport_max_date and value > self.transport_max_date:
            return False
        return True

    def covers_accommodation(self, value: date) -> bool:
        if self.accommodation_min_date and value < self.accommodation_min_date:
            return False
        if self.accommodation_max_date and value > self.accommodation_max_date:
            return False
        return True

    def as_dict(self) -> dict:
        return {
            "transport_min_date": self.transport_min_date.isoformat()
            if self.transport_min_date
            else None,
            "transport_max_date": self.transport_max_date.isoformat()
            if self.transport_max_date
            else None,
            "accommodation_min_date": self.accommodation_min_date.isoformat()
            if self.accommodation_min_date
            else None,
            "accommodation_max_date": self.accommodation_max_date.isoformat()
            if self.accommodation_max_date
            else None,
            "transport_sources": self.transport_sources,
            "accommodation_sources": self.accommodation_sources,
        }


@dataclass(slots=True)
class CityCapability:
    """Что поддерживает город: используется для проверки транспорта."""

    code: str
    name: str
    is_active: bool = True
    supports_avia: bool = True
    supports_rail: bool = True


def validate_scenario(
    scenario: ScenarioInput,
    *,
    cities: dict[str, CityCapability],
    horizon: HorizonInfo | None = None,
    profile_active: bool = True,
    today: date | None = None,
) -> ValidationResult:
    """Полная валидация сценария до обращения к источникам."""
    result = ValidationResult()
    today = today or utcnow().date()

    # --- Города --------------------------------------------------------- #
    origin = cities.get(scenario.origin_city_code)
    destination = cities.get(scenario.destination_city_code)

    if origin is None:
        result.add_error(
            "UNSUPPORTED_CITY",
            f"Город отправления {scenario.origin_city_code} не поддерживается",
            "origin_city_code",
        )
    elif not origin.is_active:
        result.add_error(
            "INACTIVE_CITY", f"Город отправления {origin.name} деактивирован", "origin_city_code"
        )

    if destination is None:
        result.add_error(
            "UNSUPPORTED_CITY",
            f"Город назначения {scenario.destination_city_code} не поддерживается",
            "destination_city_code",
        )
    elif not destination.is_active:
        result.add_error(
            "INACTIVE_CITY",
            f"Город назначения {destination.name} деактивирован",
            "destination_city_code",
        )

    if scenario.origin_city_code == scenario.destination_city_code:
        result.add_error(
            "SAME_CITY",
            "Город отправления и назначения должны различаться",
            "destination_city_code",
        )

    # --- Даты ----------------------------------------------------------- #
    # Совпадение дат означает поездку в одну сторону: обратного плеча нет.
    # Это допустимо только там, где проживание не наблюдается — бронь на ноль
    # ночей не существует, и такой сценарий ничего бы не измерил.
    is_one_way = scenario.return_date == scenario.departure_date
    if is_one_way and scenario.accommodation_type is not None:
        result.add_error(
            "ONE_WAY_WITH_ACCOMMODATION",
            "Поездка в одну сторону не может наблюдать проживание: "
            "бронь на ноль ночей не существует",
            "return_date",
        )
    elif scenario.return_date < scenario.departure_date:
        result.add_error(
            "INVALID_DATE_ORDER",
            "Дата окончания поездки должна быть позже даты начала",
            "return_date",
        )
    elif not is_one_way:
        nights = (scenario.return_date - scenario.departure_date).days
        if nights > MAX_NIGHTS:
            result.add_error(
                "TOO_LONG",
                f"Длительность поездки {nights} ночей превышает лимит MVP ({MAX_NIGHTS})",
                "return_date",
            )

    if scenario.departure_date < today:
        result.add_error(
            "PAST_DATE",
            f"Дата начала поездки {scenario.departure_date} уже прошла",
            "departure_date",
        )

    # --- Горизонт источников -------------------------------------------- #
    # Горизонт проверяется только у наблюдаемых компонент: отсутствие
    # источника проживания не должно отклонять сценарий, который следит
    # исключительно за перелетом.
    if horizon is not None and scenario.transport_type is not None:
        if not horizon.has_transport:
            result.add_error(
                "NO_TRANSPORT_SOURCE",
                "Нет ни одного пригодного источника транспорта",
                "transport_type",
            )
        elif not horizon.covers_transport(scenario.departure_date) or not horizon.covers_transport(
            scenario.return_date
        ):
            result.add_error(
                "OUT_OF_HORIZON",
                "Даты поездки вне поддерживаемого горизонта транспортных источников "
                f"({horizon.transport_min_date} – {horizon.transport_max_date})",
                "departure_date",
            )

    if horizon is not None and scenario.accommodation_type is not None:
        if not horizon.has_accommodation:
            result.add_error(
                "NO_ACCOMMODATION_SOURCE",
                "Нет ни одного пригодного источника проживания",
                "accommodation_type",
            )
        elif not horizon.covers_accommodation(
            scenario.departure_date
        ) or not horizon.covers_accommodation(scenario.return_date):
            result.add_error(
                "OUT_OF_HORIZON",
                "Даты поездки вне поддерживаемого горизонта источников проживания "
                f"({horizon.accommodation_min_date} – {horizon.accommodation_max_date})",
                "departure_date",
            )

    # --- Транспорт ------------------------------------------------------- #
    if scenario.transport_type is None:
        # Компонента не наблюдается: тариф, класс вагона и поддержка городов
        # проверке не подлежат — их просто нет в этом сценарии.
        pass
    elif scenario.transport_type == TransportType.AVIA:
        if scenario.flight_fare_type is None:
            result.add_error(
                "MISSING_FARE_TYPE", "Для авиаперелета не задан авиатариф", "flight_fare_type"
            )
        if scenario.rail_class is not None:
            result.add_warning(
                "IRRELEVANT_RAIL_CLASS",
                "Класс ЖД игнорируется для авиасценария",
                "rail_class",
            )
        for city, field_name in ((origin, "origin_city_code"), (destination, "destination_city_code")):
            if city is not None and not city.supports_avia:
                result.add_error(
                    "TRANSPORT_UNAVAILABLE",
                    f"Авиасообщение для города {city.name} не поддерживается",
                    field_name,
                )
    else:
        if scenario.rail_class is None:
            result.add_error("MISSING_RAIL_CLASS", "Для поездки на поезде не задан класс", "rail_class")
        if scenario.flight_fare_type is not None:
            result.add_warning(
                "IRRELEVANT_FARE_TYPE", "Авиатариф игнорируется для ЖД-сценария", "flight_fare_type"
            )
        for city, field_name in ((origin, "origin_city_code"), (destination, "destination_city_code")):
            if city is not None and not city.supports_rail:
                result.add_error(
                    "TRANSPORT_UNAVAILABLE",
                    f"Железнодорожное сообщение для города {city.name} не поддерживается",
                    field_name,
                )

    # --- Размещение ------------------------------------------------------ #
    if scenario.accommodation_type is None:
        if scenario.transport_type is None:
            result.add_error(
                "NO_OBSERVED_COMPONENT",
                "Сценарий должен наблюдать хотя бы одну компоненту: транспорт или проживание",
                "accommodation_type",
            )
    elif scenario.accommodation_type not in SELECTABLE_ACCOMMODATION_TYPES:
        result.add_error(
            "UNSUPPORTED_ACCOMMODATION_TYPE",
            f"Тип размещения {scenario.accommodation_type} недоступен в конструкторе",
            "accommodation_type",
        )

    if (
        scenario.accommodation_type is not None
        and scenario.stars.numeric is not None
        and scenario.accommodation_type not in STARRED_ACCOMMODATION_TYPES
    ):
        result.add_error(
            "STARS_NOT_APPLICABLE",
            f"Звездность неприменима для типа размещения {scenario.accommodation_type}",
            "stars",
        )
    if (
        scenario.stars == StarsFilter.NOT_APPLICABLE
        and scenario.accommodation_type in STARRED_ACCOMMODATION_TYPES
    ):
        result.add_warning(
            "STARS_APPLICABLE",
            "Для гостиниц звездность применима — значение NOT_APPLICABLE снизит выборку",
            "stars",
        )

    # --- Состав туристов ------------------------------------------------- #
    if scenario.adults < 1:
        result.add_error("NO_ADULTS", "В поездке должен быть минимум один взрослый", "adults")
    elif scenario.adults > MAX_ADULTS:
        result.add_error(
            "TOO_MANY_ADULTS", f"Максимум взрослых в MVP — {MAX_ADULTS}", "adults"
        )

    if len(scenario.children_ages) > MAX_CHILDREN:
        result.add_error(
            "TOO_MANY_CHILDREN", f"Максимум детей в MVP — {MAX_CHILDREN}", "children_ages"
        )
    for age in scenario.children_ages:
        if age < 0 or age > MAX_CHILD_AGE:
            result.add_error(
                "INVALID_CHILD_AGE",
                f"Возраст ребенка {age} вне допустимого диапазона 0–{MAX_CHILD_AGE}",
                "children_ages",
            )

    # Вместимость номера: один номер на весь состав (базовый и семейный профили).
    traveler_count = scenario.adults + len(scenario.children_ages)
    if traveler_count > 4:
        result.add_warning(
            "CAPACITY_RISK",
            f"Состав из {traveler_count} человек редко размещается в одном номере — "
            "выборка предложений может оказаться малой",
            "adults",
        )

    # --- Питание и отмена ------------------------------------------------ #
    if not MealType.has(str(scenario.meal_type)):
        result.add_error("INVALID_MEAL_TYPE", "Некорректный тип питания", "meal_type")
    if not CancellationFilter.has(str(scenario.cancellation_filter)):
        result.add_error(
            "INVALID_CANCELLATION", "Некорректное условие отмены", "cancellation_filter"
        )

    # --- Профиль --------------------------------------------------------- #
    if not profile_active:
        result.add_error(
            "PROFILE_NOT_ACTIVE",
            "Профиль расчета не активен — расчет невозможен",
            "calculation_profile_id",
        )

    return result
