"""Контракт между коннекторами и движком.

Коннектор не может передавать произвольную структуру напрямую в Calculation
Engine (DELTA §10.4). Он обязан вернуть объекты описанных ниже типов; все
неизвестные поля источника допускаются только в ``source_payload``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tco.core.enums import ConnectorOutcome, OfferType, TransportType
from tco.version import SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Запрос к источнику
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TransportQuery:
    """Нормализованный запрос транспорта. Города — доменные объекты, не URL."""

    origin_city_code: str
    destination_city_code: str
    origin_city_name: str
    destination_city_name: str
    departure_date: date
    return_date: date
    adults: int
    children_ages: tuple[int, ...]
    transport_type: TransportType
    #: Идентификаторы города в конкретном источнике (IATA, коды станций, geo_id).
    origin_source_ids: dict[str, Any] = field(default_factory=dict)
    destination_source_ids: dict[str, Any] = field(default_factory=dict)
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    destination_latitude: float | None = None
    destination_longitude: float | None = None

    @property
    def traveler_count(self) -> int:
        return self.adults + len(self.children_ages)


@dataclass(frozen=True, slots=True)
class AccommodationQuery:
    """Нормализованный запрос проживания."""

    city_code: str
    city_name: str
    check_in: date
    check_out: date
    adults: int
    children_ages: tuple[int, ...]
    accommodation_type: str
    stars: str
    meal_type: str
    cancellation_filter: str
    city_source_ids: dict[str, Any] = field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def guest_count(self) -> int:
        return self.adults + len(self.children_ages)


# --------------------------------------------------------------------------- #
# Предложения источника
# --------------------------------------------------------------------------- #


class ProviderSegment(BaseModel):
    """Один сегмент плеча (перелета/поездки)."""

    model_config = ConfigDict(extra="forbid")

    origin_code: str | None = None
    origin_name: str | None = None
    origin_city_name: str | None = None
    destination_code: str | None = None
    destination_name: str | None = None
    destination_city_name: str | None = None
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    duration_minutes: int | None = None
    carrier_code: str | None = None
    carrier_name: str | None = None
    vehicle_number: str | None = None
    vehicle_name: str | None = None
    aircraft: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump()
        for key in ("departure_at", "arrival_at"):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.isoformat()
        return payload


class ProviderOfferBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    source_offer_id: str | None = None
    currency: str = "RUB"
    #: Итоговая цена в единицах, определенных ниже полем ``price_basis``.
    total_price: Decimal | None = None
    price_basis: Literal["ALL_PASSENGERS", "PER_PASSENGER", "PER_ROOM_TOTAL", "PER_NIGHT"] = (
        "ALL_PASSENGERS"
    )
    deeplink: str | None = None
    #: Неизвестные/сырые поля источника. В движок не попадают.
    source_payload: dict[str, Any] = Field(default_factory=dict)


class ProviderFlightOffer(ProviderOfferBase):
    """Авиапредложение в контракте коннектора."""

    offer_type: Literal[OfferType.FLIGHT] = OfferType.FLIGHT

    origin_code: str | None = None
    destination_code: str | None = None
    origin_name: str | None = None
    destination_name: str | None = None

    outbound_segments: list[ProviderSegment] = Field(default_factory=list)
    inbound_segments: list[ProviderSegment] = Field(default_factory=list)
    outbound_duration_minutes: int | None = None
    inbound_duration_minutes: int | None = None

    cabin_class: str | None = None
    fare_family: str | None = None
    #: Сырая строка о багаже — источник классификации (нормализация решает тип).
    baggage_raw: str | None = None
    refund_raw: str | None = None
    fare_conditions_raw: str | None = None
    passenger_count: int = 1
    is_round_trip: bool = True


class ProviderRailOffer(ProviderOfferBase):
    """ЖД-предложение в контракте коннектора."""

    offer_type: Literal[OfferType.RAIL] = OfferType.RAIL

    origin_station_code: str | None = None
    destination_station_code: str | None = None
    origin_station_name: str | None = None
    destination_station_name: str | None = None
    origin_city_name: str | None = None
    destination_city_name: str | None = None

    outbound_segments: list[ProviderSegment] = Field(default_factory=list)
    inbound_segments: list[ProviderSegment] = Field(default_factory=list)
    outbound_train_number: str | None = None
    inbound_train_number: str | None = None
    outbound_duration_minutes: int | None = None
    inbound_duration_minutes: int | None = None

    carriers: list[str] = Field(default_factory=list)
    #: Сырое название типа вагона (КУПЕ/ПЛАЦ/Compartment/ReservedSeat).
    car_type_raw: str | None = None
    service_classes: list[str] = Field(default_factory=list)
    available_places_outbound: int | None = None
    available_places_inbound: int | None = None
    is_two_storey: bool | None = None
    refund_raw: str | None = None

    #: Цена одного места в каждом направлении — база для пересчета на пассажиров.
    price_per_place_outbound: Decimal | None = None
    price_per_place_inbound: Decimal | None = None
    passenger_count: int = 1
    is_round_trip: bool = True
    price_basis: Literal["ALL_PASSENGERS", "PER_PASSENGER", "PER_ROOM_TOTAL", "PER_NIGHT"] = (
        "PER_PASSENGER"
    )


class ProviderAccommodationOffer(ProviderOfferBase):
    """Предложение по проживанию в контракте коннектора."""

    offer_type: Literal[OfferType.ACCOMMODATION] = OfferType.ACCOMMODATION
    price_basis: Literal["ALL_PASSENGERS", "PER_PASSENGER", "PER_ROOM_TOTAL", "PER_NIGHT"] = (
        "PER_ROOM_TOTAL"
    )

    property_source_id: str | None = None
    property_name: str | None = None
    accommodation_type_raw: str | None = None
    stars: int | None = None
    #: Гостиница без официальной классификации отличается от «звезды неизвестны».
    stars_unrated: bool = False
    address: str | None = None
    city_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    check_in: date | None = None
    check_out: date | None = None
    nights: int | None = None

    room_name: str | None = None
    room_count: int = 1
    max_guests: int | None = None
    #: Источник подтвердил вместимость тем, что поиск шел по составу гостей.
    capacity_confirmed_by_query: bool = False
    price_per_night: Decimal | None = None

    meal_raw: str | None = None
    cancellation_raw: str | None = None
    review_score: float | None = None
    review_count: int | None = None
    amenities: list[str] = Field(default_factory=list)


ProviderOffer = ProviderFlightOffer | ProviderRailOffer | ProviderAccommodationOffer


# --------------------------------------------------------------------------- #
# Артефакты и результат
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RawArtifact:
    """Сырой ответ, подлежащий сохранению."""

    payload: bytes | str | dict | list
    endpoint: str | None = None
    request_params: dict[str, Any] = field(default_factory=dict)
    http_status: int | None = None
    content_type: str = "application/json"
    latency_ms: int | None = None


@dataclass(slots=True)
class HtmlArtifact:
    """HTML-снимок страницы (обязателен для браузерных источников, DELTA §3)."""

    html: str
    final_url: str | None = None
    http_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    screenshot: bytes | None = None
    extraction_log: list[str] = field(default_factory=list)
    parser_version: str | None = None


@dataclass(slots=True)
class ConnectorResult:
    """Единый контракт ответа коннектора.

    Ошибка источника всегда возвращается как результат с ``outcome != SUCCESS``
    и никогда не обрушивает расчет (SCOPE-R E §3).
    """

    source_code: str
    offer_type: OfferType
    outcome: ConnectorOutcome
    offers: list[ProviderOffer] = field(default_factory=list)
    raw_artifacts: list[RawArtifact] = field(default_factory=list)
    html_artifacts: list[HtmlArtifact] = field(default_factory=list)
    latency_ms: int | None = None
    attempts: int = 1
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    connector_version: str = "1.0.0"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    #: Сбор прерван добровольно — по бюджету времени или по потолку выдачи
    #: источника. Предложения при этом настоящие, но выборка неполна, и
    #: показатель по ней описывает рынок хуже обычного. Отличается от ошибки:
    #: обращение состоялось и данные получены.
    is_partial: bool = False

    @property
    def is_success(self) -> bool:
        return self.outcome.is_ok

    @property
    def offer_count(self) -> int:
        return len(self.offers)

    @classmethod
    def failure(
        cls,
        *,
        source_code: str,
        offer_type: OfferType,
        outcome: ConnectorOutcome,
        error_code: str,
        error_message: str,
        connector_version: str = "1.0.0",
        latency_ms: int | None = None,
        attempts: int = 1,
        raw_artifacts: list[RawArtifact] | None = None,
    ) -> ConnectorResult:
        return cls(
            source_code=source_code,
            offer_type=offer_type,
            outcome=outcome,
            error_code=error_code,
            error_message=error_message[:1000] if error_message else None,
            connector_version=connector_version,
            latency_ms=latency_ms,
            attempts=attempts,
            raw_artifacts=raw_artifacts or [],
        )
