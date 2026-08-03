"""Нормализация предложений источников в единую модель.

Здесь предложение получает:

* цену в единицах методики (транспорт — все пассажиры туда и обратно,
  проживание — один номер за весь период);
* статус валидности и полноты классификации;
* технический отпечаток для дедупликации;
* ключ межисточниковой эквивалентности.

Фильтрация по профилю выполняется отдельным шагом движка: нормализация
описывает предложение, но не решает, участвует ли оно в расчете.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tco.core.enums import (
    AccommodationType,
    BaggageType,
    ClassificationStatus,
    OfferType,
    RailClass,
    TransportType,
    ValidityStatus,
)
from tco.core.utils import money, property_key, stable_hash, stable_uuid, to_decimal, utcnow
from tco.connectors.contracts import (
    ProviderAccommodationOffer,
    ProviderFlightOffer,
    ProviderOffer,
    ProviderRailOffer,
)
from tco.db.models.offer import AccommodationOffer, FlightOffer, Offer, RailOffer
from tco.normalization.classify import (
    classify_accommodation_type,
    classify_baggage,
    classify_cancellation,
    classify_meal,
    classify_rail_class,
    classify_refundability,
    stars_status,
)
from tco.schemas.profile import ProfileRules
from tco.version import NORMALIZATION_VERSION


@dataclass(slots=True)
class NormalizationContext:
    """Контекст, в котором интерпретируется предложение."""

    scenario_id: Any
    market_snapshot_id: Any
    source_id: Any
    source_code: str
    connector_version: str
    transport_type: TransportType
    accommodation_type: AccommodationType
    departure_date: date
    return_date: date
    adults: int
    children_ages: tuple[int, ...]
    base_currency: str = "RUB"
    rules: ProfileRules = field(default_factory=ProfileRules)
    collected_at: datetime = field(default_factory=utcnow)
    raw_response_id: Any = None
    html_snapshot_id: Any = None
    raw_object_ref: str | None = None

    @property
    def traveler_count(self) -> int:
        return self.adults + len(self.children_ages)

    @property
    def nights(self) -> int:
        return (self.return_date - self.departure_date).days


@dataclass(slots=True)
class NormalizedOffer:
    """Результат нормализации: ORM-объект плюс диагностика."""

    offer: Offer
    messages: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.offer.validity_status == ValidityStatus.VALID.value


def rail_passenger_multiplier(
    adults: int, children_ages: tuple[int, ...], rules: ProfileRules
) -> Decimal:
    """Множитель пересчета цены места в цену для всего состава туристов.

    Детский ЖД-тариф по умолчанию не моделируется (коэффициент 1.0) —
    это осознанное ограничение, см. ``docs/LIMITATIONS.md``.
    """
    pricing = rules.transport_pricing
    multiplier = Decimal(str(adults))
    child_ratio = Decimal(str(pricing.rail_child_fare_ratio))
    for age in children_ages:
        multiplier += child_ratio if age <= pricing.rail_child_max_age else Decimal("1")
    return multiplier


# --------------------------------------------------------------------------- #
# Публичная точка входа
# --------------------------------------------------------------------------- #


def normalize(offer: ProviderOffer, context: NormalizationContext) -> NormalizedOffer:
    if isinstance(offer, ProviderFlightOffer):
        return _normalize_flight(offer, context)
    if isinstance(offer, ProviderRailOffer):
        return _normalize_rail(offer, context)
    if isinstance(offer, ProviderAccommodationOffer):
        return _normalize_accommodation(offer, context)
    raise TypeError(f"Неизвестный тип предложения: {type(offer)!r}")


def normalize_many(
    offers: list[ProviderOffer], context: NormalizationContext
) -> list[NormalizedOffer]:
    return [normalize(offer, context) for offer in offers]


# --------------------------------------------------------------------------- #
# Общая часть
# --------------------------------------------------------------------------- #


def _base_offer(
    provider: ProviderOffer,
    context: NormalizationContext,
    offer_type: OfferType,
    total_price: Decimal | None,
) -> Offer:
    return Offer(
        market_snapshot_id=context.market_snapshot_id,
        scenario_id=context.scenario_id,
        source_id=context.source_id,
        source_code=context.source_code,
        source_offer_id=(provider.source_offer_id or "")[:255] or None,
        offer_type=offer_type.value,
        collected_at=context.collected_at,
        currency=(provider.currency or context.base_currency).upper()[:3],
        total_price=total_price,
        validity_status=ValidityStatus.VALID.value,
        classification_status=ClassificationStatus.CLASSIFIED.value,
        validation_messages=[],
        technical_fingerprint="",
        raw_object_ref=context.raw_object_ref,
        raw_response_id=context.raw_response_id,
        html_snapshot_id=context.html_snapshot_id,
        deeplink=(provider.deeplink or "")[:2048] or None,
        normalization_version=NORMALIZATION_VERSION,
        connector_version=context.connector_version,
        source_metadata=dict(provider.source_payload or {}),
    )


def _apply_common_validation(
    offer: Offer, context: NormalizationContext, messages: list[str]
) -> None:
    """Проверки цены и валюты, общие для всех типов предложений."""
    rules = context.rules.filters
    price = to_decimal(offer.total_price)

    if price is None:
        offer.validity_status = ValidityStatus.INVALID_PRICE.value
        messages.append("Цена отсутствует")
    elif price <= 0:
        offer.validity_status = ValidityStatus.INVALID_PRICE.value
        messages.append(f"Некорректная цена: {price}")
    elif price < Decimal(str(rules.min_price)):
        offer.validity_status = ValidityStatus.INVALID_PRICE.value
        messages.append(f"Цена ниже допустимого минимума: {price}")
    elif rules.max_price is not None and price > Decimal(str(rules.max_price)):
        offer.validity_status = ValidityStatus.INVALID_PRICE.value
        messages.append(f"Цена выше допустимого максимума: {price}")

    if rules.require_base_currency and offer.currency != context.base_currency:
        # Конвертация валют в MVP не выполняется — предложение отбраковывается.
        offer.validity_status = ValidityStatus.INVALID_CURRENCY.value
        messages.append(f"Валюта {offer.currency} отличается от базовой {context.base_currency}")


def _finalize(
    normalized: NormalizedOffer,
    fingerprint_payload: dict,
    equivalence_payload: dict | None,
) -> NormalizedOffer:
    offer = normalized.offer
    offer.technical_fingerprint = stable_hash(fingerprint_payload)
    if equivalence_payload is not None:
        key = stable_hash(equivalence_payload)
        offer.equivalence_key = key[:128]
        offer.equivalence_group_id = stable_uuid(equivalence_payload)
    offer.validation_messages = normalized.messages
    return normalized


def _set_classification(offer: Offer, status: ClassificationStatus) -> None:
    """Понижает статус классификации, сохраняя наиболее значимую проблему."""
    priority = {
        ClassificationStatus.CLASSIFIED: 0,
        ClassificationStatus.UNCLASSIFIED_CANCELLATION: 1,
        ClassificationStatus.UNCLASSIFIED_MEAL: 2,
        ClassificationStatus.UNCLASSIFIED_CAPACITY: 3,
        ClassificationStatus.UNCLASSIFIED_FARE: 4,
    }
    current = ClassificationStatus(offer.classification_status)
    if priority[status] > priority[current]:
        offer.classification_status = status.value


# --------------------------------------------------------------------------- #
# Авиа
# --------------------------------------------------------------------------- #


def _normalize_flight(provider: ProviderFlightOffer, context: NormalizationContext) -> NormalizedOffer:
    messages: list[str] = []

    price = to_decimal(provider.total_price)
    if price is not None and provider.price_basis == "PER_PASSENGER":
        price = price * context.traveler_count
    total_price = money(price)

    offer = _base_offer(provider, context, OfferType.FLIGHT, total_price)
    _apply_common_validation(offer, context, messages)

    baggage = classify_baggage(provider.baggage_raw, provider.fare_family)
    if baggage == BaggageType.UNKNOWN:
        _set_classification(offer, ClassificationStatus.UNCLASSIFIED_FARE)
        messages.append("Багаж не классифицирован")

    outbound = [segment.as_dict() for segment in provider.outbound_segments]
    inbound = [segment.as_dict() for segment in provider.inbound_segments]

    if not outbound:
        offer.validity_status = ValidityStatus.INVALID_ROUTE.value
        messages.append("Отсутствуют сегменты плеча «туда»")
    elif not inbound:
        # Сценарий круговой: одностороннее предложение несопоставимо и
        # не должно участвовать в агрегации.
        offer.validity_status = ValidityStatus.INVALID_ROUTE.value
        messages.append("Отсутствует плечо «обратно» — предложение несопоставимо со сценарием")

    outbound_departure = _first_datetime(provider.outbound_segments, "departure_at")
    if outbound_departure and outbound_departure.date() != context.departure_date:
        messages.append(
            f"Дата вылета {outbound_departure.date()} не совпадает с датой сценария "
            f"{context.departure_date}"
        )

    max_stops = context.rules.filters.max_stops
    stops_out = max(0, len(outbound) - 1)
    stops_in = max(0, len(inbound) - 1)

    offer.flight = FlightOffer(
        origin_code=_trim(provider.origin_code, 8),
        destination_code=_trim(provider.destination_code, 8),
        origin_name=_trim(provider.origin_name, 255),
        destination_name=_trim(provider.destination_name, 255),
        outbound_departure_at=outbound_departure,
        outbound_arrival_at=_last_datetime(provider.outbound_segments, "arrival_at"),
        inbound_departure_at=_first_datetime(provider.inbound_segments, "departure_at"),
        inbound_arrival_at=_last_datetime(provider.inbound_segments, "arrival_at"),
        outbound_segments=outbound,
        inbound_segments=inbound,
        outbound_stops=stops_out,
        inbound_stops=stops_in,
        outbound_duration_minutes=provider.outbound_duration_minutes
        or _sum_durations(provider.outbound_segments),
        inbound_duration_minutes=provider.inbound_duration_minutes
        or _sum_durations(provider.inbound_segments),
        marketing_carriers=sorted(
            {segment.carrier_code or segment.carrier_name or "" for segment in provider.outbound_segments}
            - {""}
        ),
        flight_numbers=[
            segment.vehicle_number
            for segment in [*provider.outbound_segments, *provider.inbound_segments]
            if segment.vehicle_number
        ],
        cabin_class=_trim(provider.cabin_class, 32),
        fare_family=_trim(provider.fare_family, 128),
        baggage_type=baggage.value,
        baggage_raw=_trim(provider.baggage_raw, 255),
        refundability=classify_refundability(provider.refund_raw).value,
        is_round_trip=bool(outbound and inbound),
        passenger_count=context.traveler_count,
        price_basis="ALL_PASSENGERS",
    )

    if max_stops is not None and max(stops_out, stops_in) > max_stops:
        messages.append(f"Число пересадок {max(stops_out, stops_in)} превышает лимит профиля {max_stops}")

    normalized = NormalizedOffer(offer=offer, messages=messages)
    fingerprint = {
        "type": "FLIGHT",
        "source": context.source_code,
        "outbound": _segment_signature(provider.outbound_segments),
        "inbound": _segment_signature(provider.inbound_segments),
        "price": str(total_price),
        "baggage": baggage.value,
        "fare_family": provider.fare_family,
        "cabin": provider.cabin_class,
    }
    # Ключ эквивалентности не включает цену и источник: одно и то же
    # физическое предложение у разных источников должно попасть в одну группу.
    equivalence = {
        "type": "FLIGHT",
        "outbound": _segment_signature(provider.outbound_segments),
        "inbound": _segment_signature(provider.inbound_segments),
        "baggage": baggage.value,
    }
    return _finalize(normalized, fingerprint, equivalence)


# --------------------------------------------------------------------------- #
# ЖД
# --------------------------------------------------------------------------- #


def _normalize_rail(provider: ProviderRailOffer, context: NormalizationContext) -> NormalizedOffer:
    messages: list[str] = []
    multiplier = rail_passenger_multiplier(context.adults, context.children_ages, context.rules)

    out_place = to_decimal(provider.price_per_place_outbound)
    in_place = to_decimal(provider.price_per_place_inbound)

    if out_place is not None and in_place is not None:
        total = (out_place + in_place) * multiplier
    else:
        raw_total = to_decimal(provider.total_price)
        if raw_total is None:
            total = None
        elif provider.price_basis == "PER_PASSENGER":
            total = raw_total * multiplier
        else:
            total = raw_total
    total_price = money(total)

    offer = _base_offer(provider, context, OfferType.RAIL, total_price)
    _apply_common_validation(offer, context, messages)

    rail_class = classify_rail_class(provider.car_type_raw)
    if rail_class is None:
        _set_classification(offer, ClassificationStatus.UNCLASSIFIED_FARE)
        messages.append(
            f"Класс вагона не поддерживается или не определен: {provider.car_type_raw!r}"
        )

    if not provider.is_round_trip or not (provider.outbound_segments and provider.inbound_segments):
        offer.validity_status = ValidityStatus.INVALID_ROUTE.value
        messages.append("Предложение не покрывает поездку туда и обратно")

    offer.rail = RailOffer(
        origin_station_code=_trim(provider.origin_station_code, 16),
        destination_station_code=_trim(provider.destination_station_code, 16),
        origin_station_name=_trim(provider.origin_station_name, 255),
        destination_station_name=_trim(provider.destination_station_name, 255),
        origin_city_name=_trim(provider.origin_city_name, 128),
        destination_city_name=_trim(provider.destination_city_name, 128),
        outbound_train_number=_trim(provider.outbound_train_number, 32),
        inbound_train_number=_trim(provider.inbound_train_number, 32),
        outbound_departure_at=_first_datetime(provider.outbound_segments, "departure_at"),
        outbound_arrival_at=_last_datetime(provider.outbound_segments, "arrival_at"),
        inbound_departure_at=_first_datetime(provider.inbound_segments, "departure_at"),
        inbound_arrival_at=_last_datetime(provider.inbound_segments, "arrival_at"),
        outbound_duration_minutes=provider.outbound_duration_minutes,
        inbound_duration_minutes=provider.inbound_duration_minutes,
        carriers=list(provider.carriers or []),
        car_type=rail_class.value if rail_class else None,
        car_type_raw=_trim(provider.car_type_raw, 64),
        service_classes=list(provider.service_classes or []),
        available_places_outbound=provider.available_places_outbound,
        available_places_inbound=provider.available_places_inbound,
        is_two_storey=provider.is_two_storey,
        price_per_place_outbound=money(out_place),
        price_per_place_inbound=money(in_place),
        passenger_count=context.traveler_count,
        is_round_trip=bool(provider.is_round_trip),
        refundability=classify_refundability(provider.refund_raw).value,
        segments=[
            *(segment.as_dict() for segment in provider.outbound_segments),
            *(segment.as_dict() for segment in provider.inbound_segments),
        ],
    )

    normalized = NormalizedOffer(offer=offer, messages=messages)
    fingerprint = {
        "type": "RAIL",
        "source": context.source_code,
        "outbound_train": provider.outbound_train_number,
        "inbound_train": provider.inbound_train_number,
        "outbound_departure": _iso(_first_datetime(provider.outbound_segments, "departure_at")),
        "inbound_departure": _iso(_first_datetime(provider.inbound_segments, "departure_at")),
        "car_type": rail_class.value if rail_class else provider.car_type_raw,
        "price": str(total_price),
    }
    equivalence = {
        "type": "RAIL",
        "outbound_train": _normalize_train_number(provider.outbound_train_number),
        "inbound_train": _normalize_train_number(provider.inbound_train_number),
        "outbound_date": _date_str(_first_datetime(provider.outbound_segments, "departure_at")),
        "inbound_date": _date_str(_first_datetime(provider.inbound_segments, "departure_at")),
        "car_type": rail_class.value if rail_class else None,
    }
    return _finalize(normalized, fingerprint, equivalence)


# --------------------------------------------------------------------------- #
# Проживание
# --------------------------------------------------------------------------- #


def _normalize_accommodation(
    provider: ProviderAccommodationOffer, context: NormalizationContext
) -> NormalizedOffer:
    messages: list[str] = []
    nights = provider.nights or context.nights or 1

    raw_price = to_decimal(provider.total_price)
    if raw_price is None and provider.price_per_night is not None:
        raw_price = to_decimal(provider.price_per_night) * nights
    elif raw_price is not None and provider.price_basis == "PER_NIGHT":
        raw_price = raw_price * nights
    total_price = money(raw_price)

    offer = _base_offer(provider, context, OfferType.ACCOMMODATION, total_price)
    offer.price_per_night = money(
        to_decimal(provider.price_per_night)
        if provider.price_per_night is not None
        else (raw_price / nights if raw_price is not None and nights else None)
    )
    _apply_common_validation(offer, context, messages)

    accommodation_type = classify_accommodation_type(
        provider.accommodation_type_raw, context.accommodation_type
    )
    meal = classify_meal(provider.meal_raw)
    cancellation = classify_cancellation(provider.cancellation_raw)

    from tco.core.enums import CancellationType, MealType

    if meal == MealType.UNKNOWN:
        _set_classification(offer, ClassificationStatus.UNCLASSIFIED_MEAL)
        messages.append("Тип питания не классифицирован")
    if cancellation == CancellationType.UNKNOWN:
        _set_classification(offer, ClassificationStatus.UNCLASSIFIED_CANCELLATION)
        messages.append("Условие отмены не классифицировано")

    # Вместимость: либо источник искал по составу гостей, либо явно указал
    # максимальное число гостей, покрывающее состав.
    capacity_basis: str | None = None
    capacity_confirmed = False
    if provider.max_guests is not None and provider.max_guests >= context.traveler_count:
        capacity_confirmed, capacity_basis = True, "MAX_GUESTS_FIELD"
    elif provider.max_guests is not None and provider.max_guests < context.traveler_count:
        capacity_confirmed, capacity_basis = False, "MAX_GUESTS_INSUFFICIENT"
    elif provider.capacity_confirmed_by_query:
        capacity_confirmed, capacity_basis = True, "OCCUPANCY_QUERY"

    if not capacity_confirmed:
        _set_classification(offer, ClassificationStatus.UNCLASSIFIED_CAPACITY)
        if context.rules.filters.require_capacity_confirmation:
            offer.validity_status = ValidityStatus.INVALID_CAPACITY.value
        messages.append(
            "Вместимость номера для заданного состава туристов не подтверждена"
            if capacity_basis != "MAX_GUESTS_INSUFFICIENT"
            else f"Вместимость номера ({provider.max_guests}) меньше состава ({context.traveler_count})"
        )

    check_in = provider.check_in or context.departure_date
    check_out = provider.check_out or context.return_date
    if check_in != context.departure_date or check_out != context.return_date:
        offer.validity_status = ValidityStatus.INVALID_DATES.value
        messages.append(
            f"Период размещения {check_in}–{check_out} не совпадает со сценарием "
            f"{context.departure_date}–{context.return_date}"
        )

    key = property_key(provider.property_name, provider.address)
    offer.accommodation = AccommodationOffer(
        property_source_id=_trim(provider.property_source_id, 128),
        property_name=_trim(provider.property_name, 512),
        property_key=_trim(key, 256),
        accommodation_type=accommodation_type.value,
        accommodation_type_raw=_trim(provider.accommodation_type_raw, 128),
        stars=provider.stars,
        stars_status=stars_status(provider.stars, provider.stars_unrated, accommodation_type),
        address=_trim(provider.address, 1024),
        city_name=_trim(provider.city_name, 128),
        latitude=provider.latitude,
        longitude=provider.longitude,
        check_in=check_in,
        check_out=check_out,
        nights=nights,
        room_name=_trim(provider.room_name, 512),
        room_count=provider.room_count or 1,
        max_guests=provider.max_guests,
        capacity_confirmed=capacity_confirmed,
        capacity_confirmation_basis=capacity_basis,
        adults=context.adults,
        children_ages=list(context.children_ages),
        meal_type=meal.value,
        meal_raw=_trim(provider.meal_raw, 255),
        cancellation_type=cancellation.value,
        cancellation_raw=_trim(provider.cancellation_raw, 512),
        review_score=provider.review_score,
        review_count=provider.review_count,
        amenities=list(provider.amenities or [])[:40],
    )

    normalized = NormalizedOffer(offer=offer, messages=messages)
    fingerprint = {
        "type": "ACCOMMODATION",
        "source": context.source_code,
        "property": key,
        "room": (provider.room_name or "").strip().lower(),
        "meal": meal.value,
        "cancellation": cancellation.value,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "price": str(total_price),
    }
    equivalence = {
        "type": "ACCOMMODATION",
        "property": key,
        "stars": provider.stars,
        "meal": meal.value,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    }
    return _finalize(normalized, fingerprint, equivalence)


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #


def _trim(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _first_datetime(segments: list, attr: str) -> datetime | None:
    for segment in segments:
        value = getattr(segment, attr, None)
        if value:
            return value
    return None


def _last_datetime(segments: list, attr: str) -> datetime | None:
    for segment in reversed(segments):
        value = getattr(segment, attr, None)
        if value:
            return value
    return None


def _sum_durations(segments: list) -> int | None:
    values = [segment.duration_minutes for segment in segments if segment.duration_minutes]
    return sum(values) if values else None


def _segment_signature(segments: list) -> list[dict[str, Any]]:
    """Сигнатура плеча для отпечатков: перевозчик, рейс, времена."""
    return [
        {
            "carrier": (segment.carrier_code or segment.carrier_name or "").strip().upper(),
            "number": _normalize_train_number(segment.vehicle_number),
            "departure": _iso(segment.departure_at),
            "arrival": _iso(segment.arrival_at),
            "from": (segment.origin_code or segment.origin_name or "").strip().upper(),
            "to": (segment.destination_code or segment.destination_name or "").strip().upper(),
        }
        for segment in segments
    ]


def _normalize_train_number(value: str | None) -> str | None:
    """Убирает ведущие нули и регистр: «047Й» и «47й» — один поезд."""
    if not value:
        return None
    text = str(value).strip().upper()
    return text.lstrip("0") or text


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _date_str(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None
