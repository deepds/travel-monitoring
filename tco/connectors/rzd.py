"""Коннектор РЖД (ticket.rzd.ru).

Обращается к тому же публичному JSON-API, который оборачивает пакет ``rzd-api``.
Прямая реализация выбрана сознательно: она дает контроль над таймаутами,
ретраями, сохранением сырых ответов и не привязывает продукт к стороннему
пакету без гарантий поддержки.

Особенность API — двухфазный протокол: первый запрос может вернуть ``RID``
и ``result: RID``, после чего нужно опросить тот же endpoint с этим ``RID``,
пока не придет ``result: OK``.
"""

from __future__ import annotations

import time
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory, TransportType
from tco.core.errors import ConnectorSchemaError
from tco.core.utils import normalize_text, parse_datetime, to_decimal
from tco.connectors.base import BaseConnector
from tco.connectors.contracts import (
    ConnectorResult,
    ProviderRailOffer,
    ProviderSegment,
    RawArtifact,
    TransportQuery,
)
from tco.connectors.http import ResilientHttpClient

#: Сопоставление типов вагонов РЖД с классами методики.
CAR_TYPE_MAP: dict[str, str] = {
    "compartment": "COMPARTMENT",
    "купе": "COMPARTMENT",
    "куп": "COMPARTMENT",
    "reservedseat": "RESERVED_SEAT",
    "плацкартный": "RESERVED_SEAT",
    "плац": "RESERVED_SEAT",
    "плацкарт": "RESERVED_SEAT",
}


class RzdConnector(BaseConnector):
    """Коннектор железнодорожных предложений РЖД."""

    code = "rzd"
    title = "РЖД (ticket.rzd.ru)"
    category = SourceCategory.TRANSPORT
    supported_offer_types = (OfferType.RAIL,)
    version = "1.0.0"
    requires_credentials = False
    default_allowed_hosts = ("ticket.rzd.ru",)

    PRICING_PATH = "/api/v1/railway-service/prices/train-pricing"
    SUGGEST_PATH = "/api/v1/suggests"

    def __init__(self, context) -> None:  # noqa: ANN001
        super().__init__(context)
        self.base_url = str(context.config.get("base_url") or "https://ticket.rzd.ru").rstrip("/")
        self._station_cache: dict[str, str] = {}

    def _http(self) -> ResilientHttpClient:
        return ResilientHttpClient(
            source_code=self.code,
            allowed_hosts=self.allowed_hosts,
            soft_timeout=self.context.soft_timeout,
            hard_timeout=self.context.hard_timeout,
            max_retries=self.context.max_retries,
            backoff_base=self.context.backoff_base,
            backoff_max=self.context.backoff_max,
            rate_limit_per_minute=self.context.rate_limit_per_minute,
            default_headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Referer": f"{self.base_url}/",
            },
        )

    # ------------------------------------------------------------------ #
    # Справочник станций
    # ------------------------------------------------------------------ #

    def resolve_station(self, http: ResilientHttpClient, city_name: str) -> str | None:
        """Код станции по названию города (с кэшем в рамках вызова)."""
        key = normalize_text(city_name)
        if key in self._station_cache:
            return self._station_cache[key]

        response = http.get(
            f"{self.base_url}{self.SUGGEST_PATH}",
            params={
                "GroupResults": "true",
                "RailwaySortPriority": "true",
                "MergeSuburban": "true",
                "Query": city_name,
                "Language": "ru",
                "TransportType": "rail",
            },
        )
        payload = response.json()
        code = _first_station_code(payload, key)
        if code:
            self._station_cache[key] = code
        return code

    def _station_codes(
        self, http: ResilientHttpClient, query: TransportQuery
    ) -> tuple[str | None, str | None]:
        origin = _explicit_station(query.origin_source_ids)
        destination = _explicit_station(query.destination_source_ids)
        if not origin:
            origin = self.resolve_station(http, query.origin_city_name)
        if not destination:
            destination = self.resolve_station(http, query.destination_city_name)
        return origin, destination

    # ------------------------------------------------------------------ #
    # Поиск
    # ------------------------------------------------------------------ #

    def _search_direction(
        self,
        http: ResilientHttpClient,
        origin_code: str,
        destination_code: str,
        depart_date,  # noqa: ANN001
    ) -> tuple[Any, dict[str, Any]]:
        """Один односторонний поиск с обработкой двухфазного протокола RID."""
        body = {
            "Origin": origin_code,
            "Destination": destination_code,
            "DepartDate": depart_date.strftime("%d.%m.%Y"),
            "TimeFrom": 0,
            "TimeTo": 24,
            "CarGrouping": "DontGroup",
            "GetByLocalTime": True,
            "SpecialPlacesDemand": "StandardPlacesAndForDisabledPersons",
            "GetTrainsFromSchedule": True,
            "CarIssuingType": "Passenger",
        }
        url = f"{self.base_url}{self.PRICING_PATH}"
        params = {"service_provider": "B2B_RZD"}

        response = http.post(url, json_body=body, params=params)
        payload = response.json()

        # Двухфазный протокол: RID → повторный опрос.
        attempts = 0
        while isinstance(payload, dict) and str(payload.get("result", "")).upper() == "RID":
            rid = payload.get("RID") or payload.get("rid")
            if not rid or attempts >= 6:
                break
            attempts += 1
            time.sleep(min(0.6 * attempts, 2.5))
            response = http.post(
                url,
                json_body=body,
                params={**params, "rid": rid, "RID": rid},
            )
            payload = response.json()

        if isinstance(payload, dict) and str(payload.get("result", "")).upper() not in (
            "OK",
            "",
            "NONE",
        ):
            raise ConnectorSchemaError(
                f"РЖД вернул result={payload.get('result')}: {str(payload)[:200]}",
                source_code=self.code,
            )
        return payload, body

    def collect_transport(self, query: TransportQuery) -> ConnectorResult:
        if query.transport_type != TransportType.RAIL:
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=OfferType.RAIL,
                outcome=ConnectorOutcome.UNSUPPORTED,
                error_code="UNSUPPORTED_TRANSPORT",
                error_message="Источник поддерживает только железнодорожные перевозки",
                connector_version=self.version,
            )

        started = time.perf_counter()
        with self._http() as http:
            origin_code, destination_code = self._station_codes(http, query)
            if not origin_code or not destination_code:
                raise ConnectorSchemaError(
                    "Не удалось определить коды станций "
                    f"({query.origin_city_name} → {query.destination_city_name})",
                    source_code=self.code,
                )

            outbound_payload, outbound_request = self._search_direction(
                http, origin_code, destination_code, query.departure_date
            )
            inbound_payload, inbound_request = self._search_direction(
                http, destination_code, origin_code, query.return_date
            )

            outbound = _parse_trains(outbound_payload)
            inbound = _parse_trains(inbound_payload)
            offers = self._combine(outbound, inbound, query)

            return ConnectorResult(
                source_code=self.code,
                offer_type=OfferType.RAIL,
                outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
                offers=offers,
                raw_artifacts=[
                    RawArtifact(
                        payload=outbound_payload,
                        endpoint=f"{self.base_url}{self.PRICING_PATH}:outbound",
                        request_params=outbound_request,
                    ),
                    RawArtifact(
                        payload=inbound_payload,
                        endpoint=f"{self.base_url}{self.PRICING_PATH}:inbound",
                        request_params=inbound_request,
                    ),
                ],
                latency_ms=int((time.perf_counter() - started) * 1000),
                connector_version=self.version,
                diagnostics={
                    "origin_station": origin_code,
                    "destination_station": destination_code,
                    "outbound_variants": len(outbound),
                    "inbound_variants": len(inbound),
                },
            )

    def _combine(
        self,
        outbound: list[dict[str, Any]],
        inbound: list[dict[str, Any]],
        query: TransportQuery,
    ) -> list[ProviderRailOffer]:
        """Собирает round-trip предложения из вариантов «туда» и «обратно».

        Комбинируются только варианты одного класса вагона: плацкарт и купе
        агрегируются раздельно (SCOPE-R P §4).
        """
        if not outbound or not inbound:
            return []

        per_direction = int(self.context.config.get("roundtrip_legs_per_direction", 8))
        max_combinations = int(self.context.config.get("max_roundtrip_combinations", 64))

        by_class_out = _group_by_car_type(outbound, per_direction)
        by_class_in = _group_by_car_type(inbound, per_direction)

        offers: list[ProviderRailOffer] = []
        for car_type in sorted(set(by_class_out) & set(by_class_in)):
            for out in by_class_out[car_type]:
                for back in by_class_in[car_type]:
                    if len(offers) >= max_combinations:
                        return offers
                    offers.append(self._build_offer(out, back, car_type, query))
        return offers

    def _build_offer(
        self,
        out: dict[str, Any],
        back: dict[str, Any],
        car_type: str,
        query: TransportQuery,
    ) -> ProviderRailOffer:
        out_price = to_decimal(out.get("price"))
        back_price = to_decimal(back.get("price"))
        total = (out_price or 0) + (back_price or 0) if (out_price and back_price) else None

        return ProviderRailOffer(
            source_offer_id=f"{out.get('train_number')}-{back.get('train_number')}-{car_type}",
            currency="RUB",
            total_price=total,
            price_basis="PER_PASSENGER",
            price_per_place_outbound=out_price,
            price_per_place_inbound=back_price,
            origin_station_code=out.get("origin_code"),
            destination_station_code=out.get("destination_code"),
            origin_station_name=out.get("origin_name"),
            destination_station_name=out.get("destination_name"),
            origin_city_name=query.origin_city_name,
            destination_city_name=query.destination_city_name,
            outbound_train_number=out.get("train_number"),
            inbound_train_number=back.get("train_number"),
            outbound_duration_minutes=out.get("duration_minutes"),
            inbound_duration_minutes=back.get("duration_minutes"),
            outbound_segments=[_segment_from_train(out)],
            inbound_segments=[_segment_from_train(back)],
            carriers=sorted({*(out.get("carriers") or []), *(back.get("carriers") or [])}),
            car_type_raw=car_type,
            service_classes=sorted(
                {*(out.get("service_classes") or []), *(back.get("service_classes") or [])}
            ),
            available_places_outbound=out.get("available_places"),
            available_places_inbound=back.get("available_places"),
            is_two_storey=bool(out.get("is_two_storey") or back.get("is_two_storey")),
            passenger_count=query.traveler_count,
            is_round_trip=True,
            source_payload={
                "outbound_car_type_name": out.get("car_type_name"),
                "inbound_car_type_name": back.get("car_type_name"),
            },
        )

    # ------------------------------------------------------------------ #
    # Health check
    # ------------------------------------------------------------------ #

    def health_check(self) -> ConnectorResult:
        started = time.perf_counter()
        try:
            with self._http() as http:
                code = self.resolve_station(http, "Москва")
        except Exception as exc:  # noqa: BLE001
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=OfferType.RAIL,
                outcome=ConnectorOutcome.TRANSPORT_ERROR,
                error_code=type(exc).__name__,
                error_message=str(exc),
                connector_version=self.version,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return ConnectorResult(
            source_code=self.code,
            offer_type=OfferType.RAIL,
            outcome=ConnectorOutcome.SUCCESS if code else ConnectorOutcome.EMPTY,
            latency_ms=int((time.perf_counter() - started) * 1000),
            connector_version=self.version,
            diagnostics={"moscow_station_code": code},
        )


# --------------------------------------------------------------------------- #
# Разбор ответа
# --------------------------------------------------------------------------- #


def _explicit_station(source_ids: dict[str, Any]) -> str | None:
    value = source_ids.get("station_code") or source_ids.get("express_code")
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value) if value else None


def _first_station_code(payload: Any, normalized_city: str) -> str | None:
    """Находит код станции в ответе ``/suggests``.

    Приоритет отдается точному совпадению названия города, иначе берется
    первый узел типа ``city``, иначе первый элемент с кодом.
    """
    candidates: list[tuple[int, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name") or node.get("n") or node.get("Name")
            code = node.get("code") or node.get("c") or node.get("expressCode") or node.get("Code")
            node_type = str(node.get("nodeType") or node.get("type") or "").lower()
            if name and code:
                score = 0
                if normalize_text(str(name)) == normalized_city:
                    score += 10
                if node_type in ("city", "settlement"):
                    score += 5
                candidates.append((score, str(code)))
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _parse_trains(payload: Any) -> list[dict[str, Any]]:
    """Разворачивает ответ ``train-pricing`` в плоский список «поезд × класс»."""
    trains: list[dict[str, Any]] = []
    containers = payload.get("Tp") if isinstance(payload, dict) else None
    if not isinstance(containers, list):
        containers = [payload] if isinstance(payload, dict) else []

    for container in containers:
        items = container.get("List") if isinstance(container, dict) else None
        if not isinstance(items, list):
            continue
        for train in items:
            if not isinstance(train, dict):
                continue
            if train.get("IsSaleForbidden") is True:
                continue
            base = {
                "train_number": train.get("TrainNumber") or train.get("Number"),
                "train_name": train.get("TrainName") or train.get("BrandName"),
                "origin_code": _stringify(train.get("Code0") or train.get("OriginStationCode")),
                "destination_code": _stringify(
                    train.get("Code1") or train.get("DestinationStationCode")
                ),
                "origin_name": train.get("Station0") or train.get("OriginStationName"),
                "destination_name": train.get("Station1") or train.get("DestinationStationName"),
                "departure_at": parse_datetime(
                    _join_datetime(train.get("Date0"), train.get("Time0"))
                ),
                "arrival_at": parse_datetime(_join_datetime(train.get("Date1"), train.get("Time1"))),
                "duration_minutes": _duration_minutes(train.get("TimeInWay")),
                "carriers": _carriers(train),
                "is_two_storey": bool(train.get("IsTwoStorey")),
            }

            car_groups = train.get("CarGroups") or train.get("Cars") or []
            if not isinstance(car_groups, list):
                continue
            for group in car_groups:
                if not isinstance(group, dict):
                    continue
                if group.get("IsSaleForbidden") is True:
                    continue
                car_type = _map_car_type(group)
                if car_type is None:
                    continue
                price = to_decimal(group.get("MinPrice") or group.get("Price"))
                if price is None:
                    continue
                service_classes = group.get("ServiceClasses") or group.get("ServiceClass") or []
                if isinstance(service_classes, str):
                    service_classes = [service_classes]
                trains.append(
                    {
                        **base,
                        "car_type": car_type,
                        "car_type_name": group.get("CarTypeName") or group.get("CarType"),
                        "price": price,
                        "available_places": _as_int(
                            group.get("TotalPlaceQuantity") or group.get("PlaceQuantity")
                        ),
                        "service_classes": [str(item) for item in service_classes if item],
                    }
                )
    return trains


def _map_car_type(group: dict[str, Any]) -> str | None:
    for key in ("CarType", "CarTypeName", "Type"):
        value = group.get(key)
        if not value:
            continue
        mapped = CAR_TYPE_MAP.get(normalize_text(str(value)).replace(" ", ""))
        if mapped:
            return mapped
    return None


def _group_by_car_type(
    variants: list[dict[str, Any]], per_direction: int
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        grouped.setdefault(str(variant["car_type"]), []).append(variant)
    for car_type, items in grouped.items():
        items.sort(key=lambda item: item["price"])
        grouped[car_type] = items[:per_direction]
    return grouped


def _segment_from_train(train: dict[str, Any]) -> ProviderSegment:
    return ProviderSegment(
        origin_code=train.get("origin_code"),
        origin_name=train.get("origin_name"),
        destination_code=train.get("destination_code"),
        destination_name=train.get("destination_name"),
        departure_at=train.get("departure_at"),
        arrival_at=train.get("arrival_at"),
        duration_minutes=train.get("duration_minutes"),
        carrier_name=(train.get("carriers") or [None])[0],
        vehicle_number=train.get("train_number"),
        vehicle_name=train.get("train_name"),
    )


def _carriers(train: dict[str, Any]) -> list[str]:
    for key in ("CarrierDisplayNames", "Carriers", "Carrier"):
        value = train.get(key)
        if isinstance(value, list) and value:
            return [str(item) for item in value if item]
        if isinstance(value, str) and value:
            return [value]
    return []


def _join_datetime(day: Any, clock: Any) -> str | None:
    if not day:
        return None
    return f"{day} {clock}" if clock else str(day)


def _duration_minutes(value: Any) -> int | None:
    """``TimeInWay`` приходит как ``"1:35"`` (ч:мм) или как число минут."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if ":" in text:
        parts = text.split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stringify(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
