"""Синтетический источник-песочница.

Назначение — прогон полного конвейера (сбор → снимок → расчет → дашборд) без
внешних сетевых зависимостей: приемочные и нагрузочные тесты, демонстрация,
challenge set, отладка методики.

Три принципа, которые делают его безопасным:

1. Данные детерминированы: один и тот же сценарий, дата наблюдения и код
   источника всегда дают одну и ту же выборку. Это позволяет писать точные
   тесты на агрегацию и воспроизводить расчет.
2. Источник всегда помечен ``is_synthetic``. Признак поднимается в
   ``MarketSnapshot``, ``ScenarioRun``, explainability, UI и экспорт.
3. По умолчанию профиль расчета не допускает синтетические источники
   (``filters.allow_synthetic_sources = false``), а в проде источник выключен.

Модель цены сознательно простая и прозрачная: расстояние → базовая ставка,
поправки на горизонт бронирования, сезон и день недели, смещение источника,
разброс предложений и небольшая доля выбросов.
"""

from __future__ import annotations

import math
import random
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory, TransportType
from tco.core.utils import UTC, money, stable_hash
from tco.connectors.base import BaseConnector
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    ProviderAccommodationOffer,
    ProviderFlightOffer,
    ProviderRailOffer,
    ProviderSegment,
    RawArtifact,
    TransportQuery,
)

AIRLINES = [
    ("SU", "Аэрофлот", 1.18),
    ("S7", "S7 Airlines", 1.05),
    ("U6", "Уральские авиалинии", 0.95),
    ("DP", "Победа", 0.78),
    ("N4", "Северный ветер", 0.92),
    ("UT", "ЮТэйр", 0.9),
]

RAIL_CARRIERS = ["ФПК", "ТКС", "Гранд Сервис Экспресс"]

#: Классифицируемые варианты багажа. Неопределенный багаж добавляется
#: отдельно с вероятностью ``unknown_baggage_ratio``, чтобы доля
#: неклассифицированных тарифов была ровно управляемым параметром.
BAGGAGE_VARIANTS = [
    ("Без багажа, ручная кладь 10 кг", "CABIN"),
    ("Ручная кладь 10 кг", "CABIN"),
    ("Багаж 20 кг", "CHECKED"),
    ("1 место 23 кг", "CHECKED"),
]

MEAL_VARIANTS = ["Без питания", "Завтрак", "Завтрак и ужин", "Всё включено"]

CANCELLATION_VARIANTS = [
    "Бесплатная отмена до 24 часов",
    "Невозвратный тариф",
    "Отмена со штрафом 50%",
]

PROPERTY_PREFIXES = ["Гранд Отель", "Апарт-отель", "Гостевой дом", "Хостел", "Санаторий", "Отель"]
PROPERTY_NAMES = [
    "Северная звезда",
    "Приморье",
    "Центральный",
    "Панорама",
    "Купеческий двор",
    "Лазурный берег",
    "Академия",
    "Старый город",
    "Заря",
    "Маяк",
    "Континент",
    "Верхний сад",
]

#: Тип размещения → (множитель цены, диапазон звезд).
ACCOMMODATION_PROFILE: dict[str, tuple[float, tuple[int, int]]] = {
    "HOTEL": (1.0, (2, 5)),
    "APARTMENT": (0.85, (0, 0)),
    "GUEST_HOUSE": (0.7, (0, 0)),
    "HOSTEL": (0.35, (0, 0)),
    "SANATORIUM": (1.15, (2, 4)),
    "OTHER": (0.9, (0, 0)),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _season_factor(travel_date: date) -> float:
    """Сезонность: пик в июле-августе и в новогодние даты."""
    month = travel_date.month
    base = {1: 1.10, 2: 0.92, 3: 0.95, 4: 0.95, 5: 1.05, 6: 1.12, 7: 1.25, 8: 1.22, 9: 1.02, 10: 0.93, 11: 0.9, 12: 1.15}
    factor = base.get(month, 1.0)
    if month == 12 and travel_date.day >= 25:
        factor *= 1.2
    return factor


def _lead_time_factor(lead_days: int) -> float:
    """Чем ближе дата вылета, тем дороже; очень дальний горизонт тоже дороже."""
    if lead_days <= 0:
        return 1.7
    if lead_days <= 3:
        return 1.55
    if lead_days <= 7:
        return 1.35
    if lead_days <= 14:
        return 1.18
    if lead_days <= 30:
        return 1.05
    if lead_days <= 90:
        return 1.0
    return 1.06


def _weekday_factor(travel_date: date) -> float:
    return {0: 1.0, 1: 0.97, 2: 0.97, 3: 1.02, 4: 1.10, 5: 1.08, 6: 1.05}[travel_date.weekday()]


class SandboxConnector(BaseConnector):
    """Базовый синтетический коннектор (детерминированный генератор рынка)."""

    category = SourceCategory.TRANSPORT
    supported_offer_types = (OfferType.FLIGHT, OfferType.RAIL, OfferType.ACCOMMODATION)
    version = "1.0.0"
    requires_credentials = False
    is_synthetic = True
    default_allowed_hosts = ()

    #: Систематическое смещение источника — создает реалистичное межисточниковое
    #: расхождение (у разных агрегаторов разный инвентарь и наценка).
    price_bias: float = 1.0
    #: Разброс цен внутри источника.
    spread: float = 0.22
    #: Доля предложений-выбросов (люксовые номера, бизнес-тарифы).
    outlier_ratio: float = 0.06
    #: Сколько предложений отдает источник.
    offer_count_range: tuple[int, int] = (12, 26)
    #: Доля предложений с неопределенным багажом (проверяет ветку
    #: UNCLASSIFIED_FARE, оставаясь ниже порога допуска источника).
    unknown_baggage_ratio: float = 0.12

    def _rng(self, *parts: Any) -> random.Random:
        """Детерминированный генератор: сид зависит только от смысловых полей."""
        seed_material = stable_hash(
            {
                "source": self.code,
                "salt": self.context.config.get("seed_salt", ""),
                "parts": [str(part) for part in parts],
            }
        )
        return random.Random(int(seed_material[:16], 16))

    def _observation_seed(self) -> str:
        """Окно наблюдения: цены меняются между снимками, но не внутри снимка."""
        return str(self.context.config.get("observation_seed", ""))

    # ------------------------------------------------------------------ #
    # Транспорт
    # ------------------------------------------------------------------ #

    def collect_transport(self, query: TransportQuery) -> ConnectorResult:
        started = time.perf_counter()
        offer_type = (
            OfferType.FLIGHT if query.transport_type == TransportType.AVIA else OfferType.RAIL
        )
        rng = self._rng(
            query.origin_city_code,
            query.destination_city_code,
            query.departure_date,
            query.return_date,
            query.transport_type,
            self._observation_seed(),
        )

        distance = self._distance(query)
        offers = (
            self._flights(query, rng, distance)
            if offer_type == OfferType.FLIGHT
            else self._rail(query, rng, distance)
        )

        return ConnectorResult(
            source_code=self.code,
            offer_type=offer_type,
            outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
            offers=offers,
            raw_artifacts=[
                RawArtifact(
                    payload={
                        "synthetic": True,
                        "source": self.code,
                        "distance_km": round(distance, 1),
                        "offer_count": len(offers),
                        "note": "Синтетические данные песочницы, не рыночная информация",
                    },
                    endpoint=f"sandbox://{self.code}/transport",
                    request_params={
                        "origin": query.origin_city_code,
                        "destination": query.destination_city_code,
                        "departure_date": query.departure_date.isoformat(),
                        "return_date": query.return_date.isoformat(),
                        "adults": query.adults,
                        "children_ages": list(query.children_ages),
                    },
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
            connector_version=self.version,
            diagnostics={"synthetic": True, "distance_km": round(distance, 1)},
        )

    def _distance(self, query: TransportQuery) -> float:
        if None not in (
            query.origin_latitude,
            query.origin_longitude,
            query.destination_latitude,
            query.destination_longitude,
        ):
            return max(
                200.0,
                _haversine_km(
                    float(query.origin_latitude),
                    float(query.origin_longitude),
                    float(query.destination_latitude),
                    float(query.destination_longitude),
                ),
            )
        # Расстояние неизвестно — детерминированная подстановка по кодам городов.
        rng = self._rng("distance", query.origin_city_code, query.destination_city_code)
        return rng.uniform(600, 3000)

    def _flights(
        self, query: TransportQuery, rng: random.Random, distance: float
    ) -> list[ProviderFlightOffer]:
        lead = (query.departure_date - datetime.now(UTC).date()).days
        base_one_way = (2100 + distance * 4.4) * _lead_time_factor(lead) * _season_factor(
            query.departure_date
        ) * _weekday_factor(query.departure_date) * self.price_bias

        count = rng.randint(*self.offer_count_range)
        offers: list[ProviderFlightOffer] = []
        for index in range(count):
            code, airline, airline_factor = rng.choice(AIRLINES)
            stops = 0 if rng.random() < 0.62 else rng.choice([1, 1, 2])
            stop_factor = 1.0 if stops == 0 else (0.86 if stops == 1 else 0.78)
            noise = rng.gauss(1.0, self.spread)
            noise = max(0.6, min(noise, 1.9))
            is_outlier = rng.random() < self.outlier_ratio
            outlier_factor = rng.uniform(2.6, 4.2) if is_outlier else 1.0

            baggage_text, baggage_kind = rng.choice(BAGGAGE_VARIANTS)
            if rng.random() < self.unknown_baggage_ratio:
                baggage_text, baggage_kind = "Уточняется у перевозчика", "UNKNOWN"
            baggage_factor = 1.22 if baggage_kind == "CHECKED" else 1.0

            per_passenger_one_way = (
                base_one_way * airline_factor * stop_factor * noise * baggage_factor * outlier_factor
            )
            total = per_passenger_one_way * 2 * max(1, query.traveler_count)

            outbound = self._flight_segments(
                query, rng, query.departure_date, stops, code, airline, index, forward=True
            )
            inbound = self._flight_segments(
                query, rng, query.return_date, stops, code, airline, index, forward=False
            )

            offers.append(
                ProviderFlightOffer(
                    source_offer_id=f"{self.code}-F{index:03d}",
                    currency="RUB",
                    total_price=money(total),
                    price_basis="ALL_PASSENGERS",
                    origin_code=outbound[0].origin_code,
                    destination_code=outbound[-1].destination_code,
                    origin_name=query.origin_city_name,
                    destination_name=query.destination_city_name,
                    outbound_segments=outbound,
                    inbound_segments=inbound,
                    outbound_duration_minutes=sum(s.duration_minutes or 0 for s in outbound),
                    inbound_duration_minutes=sum(s.duration_minutes or 0 for s in inbound),
                    cabin_class="ECONOMIC",
                    fare_family="Базовый" if baggage_kind != "CHECKED" else "Стандарт",
                    baggage_raw=baggage_text,
                    refund_raw="Невозвратный" if rng.random() < 0.65 else "Возвратный со сбором",
                    passenger_count=query.traveler_count,
                    is_round_trip=True,
                    source_payload={"synthetic": True, "is_seeded_outlier": is_outlier},
                )
            )
        return offers

    def _flight_segments(
        self,
        query: TransportQuery,
        rng: random.Random,
        travel_date: date,
        stops: int,
        carrier_code: str,
        carrier_name: str,
        index: int,
        *,
        forward: bool,
    ) -> list[ProviderSegment]:
        origin_code = (query.origin_source_ids.get("iata") or ["AAA"])[0] if forward else (
            query.destination_source_ids.get("iata") or ["BBB"]
        )[0]
        destination_code = (query.destination_source_ids.get("iata") or ["BBB"])[0] if forward else (
            query.origin_source_ids.get("iata") or ["AAA"]
        )[0]
        origin_city = query.origin_city_name if forward else query.destination_city_name
        destination_city = query.destination_city_name if forward else query.origin_city_name

        departure = datetime(
            travel_date.year,
            travel_date.month,
            travel_date.day,
            rng.randint(5, 21),
            rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]),
            tzinfo=UTC,
        )
        total_minutes = int(90 + rng.uniform(0.7, 1.4) * 60 + stops * rng.randint(70, 190))
        segments: list[ProviderSegment] = []
        hops = stops + 1
        cursor = departure
        for hop in range(hops):
            leg_minutes = max(45, total_minutes // hops)
            arrival = cursor + timedelta(minutes=leg_minutes)
            segments.append(
                ProviderSegment(
                    origin_code=origin_code if hop == 0 else f"HB{hop}",
                    origin_name=origin_city if hop == 0 else f"Пересадка {hop}",
                    origin_city_name=origin_city if hop == 0 else f"Пересадка {hop}",
                    destination_code=destination_code if hop == hops - 1 else f"HB{hop + 1}",
                    destination_name=destination_city if hop == hops - 1 else f"Пересадка {hop + 1}",
                    destination_city_name=destination_city if hop == hops - 1 else f"Пересадка {hop + 1}",
                    departure_at=cursor,
                    arrival_at=arrival,
                    duration_minutes=leg_minutes,
                    carrier_code=carrier_code,
                    carrier_name=carrier_name,
                    vehicle_number=f"{carrier_code}{1000 + index * 7 + hop}",
                    aircraft=rng.choice(["A320", "B738", "SU95", "A321"]),
                )
            )
            cursor = arrival + timedelta(minutes=rng.randint(55, 150))
        return segments

    def _rail(
        self, query: TransportQuery, rng: random.Random, distance: float
    ) -> list[ProviderRailOffer]:
        lead = (query.departure_date - datetime.now(UTC).date()).days
        base_place = (450 + distance * 1.55) * _lead_time_factor(lead) * _season_factor(
            query.departure_date
        ) * self.price_bias

        offers: list[ProviderRailOffer] = []
        count = rng.randint(*self.offer_count_range)
        for index in range(count):
            car_type = "COMPARTMENT" if rng.random() < 0.5 else "RESERVED_SEAT"
            class_factor = 1.85 if car_type == "COMPARTMENT" else 1.0
            noise = max(0.7, min(rng.gauss(1.0, self.spread * 0.8), 1.8))
            is_outlier = rng.random() < self.outlier_ratio
            outlier_factor = rng.uniform(2.2, 3.4) if is_outlier else 1.0

            place_out = base_place * class_factor * noise * outlier_factor
            place_back = place_out * rng.uniform(0.94, 1.08)

            train_out = f"{rng.randint(1, 199):03d}{rng.choice('АБВГЕЖИЙ')}"
            train_back = f"{rng.randint(1, 199):03d}{rng.choice('АБВГЕЖИЙ')}"
            duration = int(distance / rng.uniform(58, 78) * 60)

            departure = datetime(
                query.departure_date.year,
                query.departure_date.month,
                query.departure_date.day,
                rng.randint(0, 23),
                rng.choice([0, 12, 25, 40, 55]),
                tzinfo=UTC,
            )
            back_departure = datetime(
                query.return_date.year,
                query.return_date.month,
                query.return_date.day,
                rng.randint(0, 23),
                rng.choice([0, 12, 25, 40, 55]),
                tzinfo=UTC,
            )
            carrier = rng.choice(RAIL_CARRIERS)

            offers.append(
                ProviderRailOffer(
                    source_offer_id=f"{self.code}-R{index:03d}",
                    currency="RUB",
                    price_basis="PER_PASSENGER",
                    total_price=money(place_out + place_back),
                    price_per_place_outbound=money(place_out),
                    price_per_place_inbound=money(place_back),
                    origin_station_name=f"{query.origin_city_name}-Пасс.",
                    destination_station_name=f"{query.destination_city_name}-Пасс.",
                    origin_city_name=query.origin_city_name,
                    destination_city_name=query.destination_city_name,
                    outbound_train_number=train_out,
                    inbound_train_number=train_back,
                    outbound_duration_minutes=duration,
                    inbound_duration_minutes=duration + rng.randint(-40, 40),
                    outbound_segments=[
                        ProviderSegment(
                            origin_name=f"{query.origin_city_name}-Пасс.",
                            destination_name=f"{query.destination_city_name}-Пасс.",
                            departure_at=departure,
                            arrival_at=departure + timedelta(minutes=duration),
                            duration_minutes=duration,
                            carrier_name=carrier,
                            vehicle_number=train_out,
                        )
                    ],
                    inbound_segments=[
                        ProviderSegment(
                            origin_name=f"{query.destination_city_name}-Пасс.",
                            destination_name=f"{query.origin_city_name}-Пасс.",
                            departure_at=back_departure,
                            arrival_at=back_departure + timedelta(minutes=duration),
                            duration_minutes=duration,
                            carrier_name=carrier,
                            vehicle_number=train_back,
                        )
                    ],
                    carriers=[carrier],
                    car_type_raw=car_type,
                    service_classes=["2Э"] if car_type == "COMPARTMENT" else ["3Э"],
                    available_places_outbound=rng.randint(2, 60),
                    available_places_inbound=rng.randint(2, 60),
                    is_two_storey=rng.random() < 0.15,
                    refund_raw="Возвратный" if rng.random() < 0.7 else "Невозвратный",
                    passenger_count=query.traveler_count,
                    is_round_trip=True,
                    source_payload={"synthetic": True, "is_seeded_outlier": is_outlier},
                )
            )
        return offers

    # ------------------------------------------------------------------ #
    # Проживание
    # ------------------------------------------------------------------ #

    def collect_accommodation(self, query: AccommodationQuery) -> ConnectorResult:
        started = time.perf_counter()
        rng = self._rng(
            query.city_code,
            query.check_in,
            query.check_out,
            query.accommodation_type,
            query.stars,
            self._observation_seed(),
        )

        type_factor, star_range = ACCOMMODATION_PROFILE.get(
            query.accommodation_type, ACCOMMODATION_PROFILE["HOTEL"]
        )
        lead = (query.check_in - datetime.now(UTC).date()).days
        requested_stars = int(query.stars) if query.stars.isdigit() else None
        # `UNRATED` — гостиницы без официальной классификации. Это отдельный
        # запрос, а не «звезды неизвестны»: реальный источник по такому фильтру
        # возвращает именно неклассифицированные объекты.
        requested_unrated = query.stars == "UNRATED"
        star_factor = 1.0 if requested_stars is None else (0.55 + 0.34 * requested_stars)
        if requested_unrated:
            # Без категории объекты в среднем дешевле трехзвездочных.
            star_factor = 0.8

        base_night = (
            2600
            * type_factor
            * star_factor
            * _lead_time_factor(lead)
            * _season_factor(query.check_in)
            * self.price_bias
        )

        nights = max(1, query.nights)
        count = rng.randint(*self.offer_count_range)
        offers: list[ProviderAccommodationOffer] = []
        for index in range(count):
            noise = max(0.62, min(rng.gauss(1.0, self.spread), 1.95))
            is_outlier = rng.random() < self.outlier_ratio
            outlier_factor = rng.uniform(2.8, 4.6) if is_outlier else 1.0

            meal_raw = (
                "Завтрак"
                if query.meal_type == "BREAKFAST"
                else "Без питания"
                if query.meal_type == "NO_MEALS"
                else rng.choice(MEAL_VARIANTS)
            )
            meal_factor = {"Без питания": 1.0, "Завтрак": 1.12, "Завтрак и ужин": 1.28, "Всё включено": 1.5}[
                meal_raw
            ]
            cancellation_raw = (
                "Бесплатная отмена до 24 часов"
                if query.cancellation_filter == "FREE_CANCELLATION"
                else rng.choice(CANCELLATION_VARIANTS)
            )
            cancel_factor = 1.09 if cancellation_raw.startswith("Бесплатная") else 1.0

            per_night = base_night * noise * meal_factor * cancel_factor * outlier_factor
            total = per_night * nights

            if requested_unrated:
                stars_value: int | None = None
                unrated = True
            elif requested_stars is not None:
                stars_value = requested_stars
                unrated = False
            elif star_range == (0, 0):
                stars_value, unrated = None, True
            else:
                stars_value = rng.randint(*star_range)
                unrated = False

            name = f"{rng.choice(PROPERTY_PREFIXES)} «{rng.choice(PROPERTY_NAMES)}»"
            offers.append(
                ProviderAccommodationOffer(
                    source_offer_id=f"{self.code}-H{index:03d}",
                    property_source_id=f"{self.code}-P{index % 9:02d}",
                    property_name=name,
                    accommodation_type_raw=query.accommodation_type,
                    stars=stars_value,
                    stars_unrated=unrated,
                    address=f"{query.city_name}, ул. Примерная, {rng.randint(1, 90)}",
                    city_name=query.city_name,
                    latitude=query.latitude,
                    longitude=query.longitude,
                    currency="RUB",
                    total_price=money(total),
                    price_basis="PER_ROOM_TOTAL",
                    price_per_night=money(per_night),
                    check_in=query.check_in,
                    check_out=query.check_out,
                    nights=nights,
                    room_name=rng.choice(
                        ["Стандартный двухместный", "Улучшенный номер", "Семейный номер", "Студия"]
                    ),
                    max_guests=max(query.guest_count, rng.randint(2, 4)),
                    capacity_confirmed_by_query=True,
                    meal_raw=meal_raw,
                    cancellation_raw=cancellation_raw,
                    review_score=round(rng.uniform(6.5, 9.8), 1),
                    review_count=rng.randint(12, 1800),
                    amenities=rng.sample(
                        ["Wi-Fi", "Парковка", "Завтрак", "Кондиционер", "Бассейн", "Фитнес"],
                        k=rng.randint(2, 4),
                    ),
                    source_payload={"synthetic": True, "is_seeded_outlier": is_outlier},
                )
            )

        return ConnectorResult(
            source_code=self.code,
            offer_type=OfferType.ACCOMMODATION,
            outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
            offers=offers,
            raw_artifacts=[
                RawArtifact(
                    payload={
                        "synthetic": True,
                        "source": self.code,
                        "offer_count": len(offers),
                        "note": "Синтетические данные песочницы, не рыночная информация",
                    },
                    endpoint=f"sandbox://{self.code}/accommodation",
                    request_params={
                        "city": query.city_code,
                        "check_in": query.check_in.isoformat(),
                        "check_out": query.check_out.isoformat(),
                        "adults": query.adults,
                        "children_ages": list(query.children_ages),
                        "accommodation_type": query.accommodation_type,
                        "stars": query.stars,
                    },
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
            connector_version=self.version,
            diagnostics={"synthetic": True},
        )

    def health_check(self) -> ConnectorResult:
        return ConnectorResult(
            source_code=self.code,
            offer_type=OfferType.FLIGHT,
            outcome=ConnectorOutcome.SUCCESS,
            latency_ms=1,
            connector_version=self.version,
            diagnostics={"synthetic": True, "note": "Песочница всегда доступна"},
        )


class SandboxAlphaConnector(SandboxConnector):
    """Песочница «Альфа» — базовое ценовое смещение."""

    code = "sandbox_alpha"
    title = "Песочница «Альфа» (синтетические данные)"
    price_bias = 1.0
    spread = 0.22
    offer_count_range = (14, 28)


class SandboxBetaConnector(SandboxConnector):
    """Песочница «Бета» — иной инвентарь и наценка, дает межисточниковое расхождение."""

    code = "sandbox_beta"
    title = "Песочница «Бета» (синтетические данные)"
    price_bias = 1.09
    spread = 0.26
    offer_count_range = (10, 22)
    unknown_baggage_ratio = 0.18
