"""Коннектор Туту.ру через публичный MCP-эндпоинт.

Источник покрывает три вертикали сразу — авиа, ЖД и проживание, — не требует
авторизации и потому является опорным для MVP.

Важная особенность реализации: точные имена аргументов инструментов MCP не
зафиксированы в публичной документации, поэтому коннектор читает
``tools/list`` и сопоставляет логические поля запроса с реальной схемой
инструмента по списку кандидатов. Это делает коннектор устойчивым к
переименованию параметров и дает внятную диагностику при schema drift вместо
молчаливого пустого ответа.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory
from tco.core.errors import ConnectorSchemaError
from tco.core.utils import normalize_text, parse_date, parse_datetime, to_decimal
from tco.connectors.base import BaseConnector
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    ProviderAccommodationOffer,
    ProviderFlightOffer,
    ProviderRailOffer,
    ProviderSegment,
    RawArtifact,
)
from tco.connectors.http import ResilientHttpClient
from tco.connectors.mcp_client import McpClient

#: Кандидаты имен аргументов для каждого логического поля.
ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "origin": (
        "from",
        "from_id",
        "from_city",
        "from_city_id",
        "from_geo_id",
        "origin",
        "origin_id",
        "origin_city",
        "departure",
        "departure_city",
        "departure_point",
    ),
    "destination": (
        "to",
        "to_id",
        "to_city",
        "to_city_id",
        "to_geo_id",
        "destination",
        "destination_id",
        "destination_city",
        "arrival",
        "arrival_city",
        "arrival_point",
    ),
    "date_forward": (
        "date",
        "date_from",
        "date_forward",
        "date_there",
        "departure_date",
        "depart_date",
        "when",
    ),
    "date_back": (
        "date_back",
        "date_backward",
        "return_date",
        "back_date",
        "date_return",
        "date_to",
    ),
    "adults": ("adults", "adult", "adults_count", "passengers"),
    "children": ("children", "children_count", "child"),
    "children_ages": ("children_ages", "child_ages", "children_age", "kids_ages"),
    "infants": ("infants", "infant", "infants_count"),
    "city": ("city_name", "city", "city_id", "geo_id", "region_id", "location", "where", "place"),
    "check_in": ("check_in", "checkin", "date_from", "arrival_date", "from_date"),
    "check_out": ("check_out", "checkout", "date_to", "departure_date", "to_date"),
    "stars_min": ("stars_min", "min_stars", "stars_from"),
    "stars_max": ("stars_max", "max_stars", "stars_to"),
    "per_page": ("per_page", "limit", "page_size", "count"),
    "page": ("page", "page_number", "offset"),
}

#: Ключи, под которыми в ответах встречается список предложений.
OFFER_LIST_KEYS = ("offers", "items", "results", "data", "variants", "hotels", "list")


def _pick_arg(schema_properties: dict[str, Any], logical: str) -> str | None:
    """Выбирает реальное имя аргумента для логического поля."""
    for candidate in ARG_ALIASES.get(logical, ()):
        if candidate in schema_properties:
            return candidate
    return None


def _prop_type(schema_properties: dict[str, Any], name: str) -> str:
    prop = schema_properties.get(name) or {}
    declared = prop.get("type")
    if isinstance(declared, list):
        return str(declared[0]) if declared else "string"
    if declared:
        return str(declared)
    # anyOf / oneOf
    for key in ("anyOf", "oneOf"):
        for variant in prop.get(key, []) or []:
            if isinstance(variant, dict) and variant.get("type") not in (None, "null"):
                return str(variant["type"])
    return "string"


def _clamp_to_schema(value: Any, prop: dict[str, Any]) -> tuple[Any, str | None]:
    """Приводит числовое значение к границам, объявленным в схеме инструмента.

    Коннектор читает схему инструмента, значит обязан соблюдать и ее
    ограничения. Иначе аргумент вроде ``page_size=50`` при максимуме 30
    приводит к ошибке валидации на стороне сервера и пустой выдаче по
    формально исправному запросу.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value, None
    minimum, maximum = prop.get("minimum"), prop.get("maximum")
    if maximum is not None and value > maximum:
        return type(value)(maximum), f"уменьшено до максимума {maximum}"
    if minimum is not None and value < minimum:
        return type(value)(minimum), f"увеличено до минимума {minimum}"
    return value, None


def _coerce(value: Any, target_type: str) -> Any:
    if target_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if target_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if target_type == "string" and isinstance(value, date):
        return value.isoformat()
    if target_type == "string" and not isinstance(value, str):
        return str(value)
    return value


def _extract_offer_list(payload: Any) -> list[dict[str, Any]]:
    """Достает список предложений из ответа произвольной вложенности."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in OFFER_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and value:
            items = [item for item in value if isinstance(item, dict)]
            # `variants` мультимодального поиска содержит вложенные offers.
            if key == "variants" and items and "offers" in items[0]:
                nested: list[dict[str, Any]] = []
                for item in items:
                    nested.extend(x for x in (item.get("offers") or []) if isinstance(x, dict))
                return nested
            if items:
                return items
    # Иногда полезная нагрузка спрятана на один уровень глубже.
    for value in payload.values():
        if isinstance(value, dict):
            nested = _extract_offer_list(value)
            if nested:
                return nested
    return []


def _unwrap_offer(item: dict[str, Any]) -> dict[str, Any]:
    """Ответы приходят как ``{"offer": {...}}`` либо плоско."""
    inner = item.get("offer")
    return inner if isinstance(inner, dict) else item


def _get_path(payload: Any, *path: str, default: Any = None) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _segments(leg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = leg.get("segments")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _build_segment(raw: dict[str, Any]) -> ProviderSegment:
    origin = raw.get("from") or {}
    destination = raw.get("to") or {}
    return ProviderSegment(
        origin_code=_first_str(origin, "iata_code", "code", "station_code"),
        origin_name=_first_str(origin, "name", "title"),
        origin_city_name=_first_str(origin, "city_name", "city"),
        destination_code=_first_str(destination, "iata_code", "code", "station_code"),
        destination_name=_first_str(destination, "name", "title"),
        destination_city_name=_first_str(destination, "city_name", "city"),
        departure_at=parse_datetime(raw.get("departure_at") or raw.get("departure")),
        arrival_at=parse_datetime(raw.get("arrival_at") or raw.get("arrival")),
        duration_minutes=_as_int(raw.get("duration")),
        carrier_code=_first_str(raw, "airline_code", "carrier_code", "carrier"),
        carrier_name=_first_str(raw, "airline_name", "carrier_name"),
        vehicle_number=_first_str(raw, "flight_no", "flight_number", "train_number", "voyage_no"),
        vehicle_name=_first_str(raw, "train_name", "vehicle_name"),
        aircraft=_first_str(raw, "aircraft"),
    )


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TutuMcpConnector(BaseConnector):
    """Коннектор Туту.ру (MCP, без авторизации)."""

    code = "tutu_mcp"
    title = "Туту.ру (MCP)"
    category = SourceCategory.TRANSPORT
    supported_offer_types = (OfferType.FLIGHT, OfferType.RAIL, OfferType.ACCOMMODATION)
    version = "1.0.0"
    requires_credentials = False
    default_allowed_hosts = ("mcp.tutu.ru",)

    TOOL_AVIA = "search_avia"
    TOOL_RAIL = "search_rail"
    TOOL_HOTELS = "search_hotels"

    def __init__(self, context) -> None:  # noqa: ANN001
        super().__init__(context)
        self.endpoint = str(context.config.get("endpoint") or "https://mcp.tutu.ru/mcp")
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._geo_index: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Инфраструктура
    # ------------------------------------------------------------------ #

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
        )

    def _load_tool_schemas(self, mcp: McpClient) -> None:
        if self._tool_schemas:
            return
        for tool in mcp.list_tools():
            name = tool.get("name")
            if not name:
                continue
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
            self._tool_schemas[str(name)] = schema if isinstance(schema, dict) else {}

    def _properties(self, tool: str) -> dict[str, Any]:
        schema = self._tool_schemas.get(tool) or {}
        props = schema.get("properties")
        return props if isinstance(props, dict) else {}

    def _required(self, tool: str) -> list[str]:
        schema = self._tool_schemas.get(tool) or {}
        required = schema.get("required")
        return [str(item) for item in required] if isinstance(required, list) else []

    def _set_arg(
        self,
        args: dict[str, Any],
        props: dict[str, Any],
        logical: str,
        value: Any,
    ) -> str | None:
        """Кладет значение под реальным именем аргумента.

        Значение приводится к объявленному типу и, если схема задает границы,
        зажимается в них.
        """
        if value is None:
            return None
        name = _pick_arg(props, logical)
        if name is None:
            return None
        coerced = _coerce(value, _prop_type(props, name))
        clamped, note = _clamp_to_schema(coerced, props.get(name) or {})
        if note:
            self.log.info(
                "Аргумент приведен к границам схемы инструмента",
                argument=name,
                requested=coerced,
                applied=clamped,
                note=note,
            )
        args[name] = clamped
        return name

    def _geo_lookup(self, mcp: McpClient, city_name: str) -> Any | None:
        """Разрешает идентификатор города через ресурс ``tutu://geo``."""
        if self._geo_index is None:
            try:
                payload = mcp.read_resource("tutu://geo")
            except Exception as exc:  # noqa: BLE001 — справочник необязателен
                self.log.debug("Справочник tutu://geo недоступен", error=str(exc))
                payload = None
            self._geo_index = _build_geo_index(payload)
        return self._geo_index.get(normalize_text(city_name)) if self._geo_index else None

    def _city_argument(
        self,
        mcp: McpClient,
        props: dict[str, Any],
        logical: str,
        city_name: str,
        source_ids: dict[str, Any],
        iata: str | None = None,
    ) -> Any:
        """Готовит значение города: id из справочника, IATA или название."""
        arg_name = _pick_arg(props, logical)
        expected = _prop_type(props, arg_name) if arg_name else "string"

        explicit = source_ids.get("city_id") or source_ids.get("geo_id")
        if explicit is not None:
            return explicit
        if expected == "integer":
            resolved = self._geo_lookup(mcp, city_name)
            if resolved is not None:
                return resolved
        if iata and expected == "string":
            return iata
        resolved = self._geo_lookup(mcp, city_name)
        if resolved is not None and expected == "integer":
            return resolved
        return city_name

    # ------------------------------------------------------------------ #
    # Сбор транспорта
    # ------------------------------------------------------------------ #

    def collect_transport(self, query) -> ConnectorResult:  # noqa: ANN001
        from tco.core.enums import TransportType

        is_avia = query.transport_type == TransportType.AVIA
        tool = self.TOOL_AVIA if is_avia else self.TOOL_RAIL
        offer_type = OfferType.FLIGHT if is_avia else OfferType.RAIL

        started = time.perf_counter()
        with self._http() as http:
            mcp = McpClient(http, self.endpoint, client_name="travel-cost-observatory")
            self._load_tool_schemas(mcp)
            if tool not in self._tool_schemas:
                raise ConnectorSchemaError(
                    f"MCP-сервер не предоставляет инструмент {tool}",
                    source_code=self.code,
                )

            props = self._properties(tool)
            args: dict[str, Any] = {}
            mapped_origin = self._set_arg(
                args,
                props,
                "origin",
                self._city_argument(
                    mcp,
                    props,
                    "origin",
                    query.origin_city_name,
                    query.origin_source_ids,
                    _first_iata(query.origin_source_ids) if is_avia else None,
                ),
            )
            mapped_destination = self._set_arg(
                args,
                props,
                "destination",
                self._city_argument(
                    mcp,
                    props,
                    "destination",
                    query.destination_city_name,
                    query.destination_source_ids,
                    _first_iata(query.destination_source_ids) if is_avia else None,
                ),
            )
            if not mapped_origin or not mapped_destination:
                raise ConnectorSchemaError(
                    f"Не удалось сопоставить параметры маршрута со схемой {tool}: "
                    f"доступные аргументы {sorted(props)}",
                    source_code=self.code,
                )

            self._set_arg(args, props, "date_forward", query.departure_date)
            has_return_arg = self._set_arg(args, props, "date_back", query.return_date) is not None
            self._set_arg(args, props, "adults", query.adults)
            children = [age for age in query.children_ages if age >= 2]
            infants = [age for age in query.children_ages if age < 2]
            if children:
                self._set_arg(args, props, "children", len(children))
                self._set_arg(args, props, "children_ages", list(children))
            if infants:
                self._set_arg(args, props, "infants", len(infants))
            self._set_arg(args, props, "per_page", int(self.context.config.get("per_page", 50)))

            raw_artifacts: list[RawArtifact] = []
            offers: list[Any] = []

            if has_return_arg:
                payload = mcp.call_tool(tool, args)
                raw_artifacts.append(
                    RawArtifact(
                        payload=payload,
                        endpoint=f"{self.endpoint}#{tool}",
                        request_params=args,
                        content_type="application/json",
                    )
                )
                offers = self._parse_transport(payload, query, offer_type, round_trip=True)
            else:
                # Инструмент не принимает обратную дату — собираем round-trip
                # из двух односторонних поисков.
                outbound_args = dict(args)
                inbound_args = dict(args)
                origin_name = _pick_arg(props, "origin")
                destination_name = _pick_arg(props, "destination")
                inbound_args[origin_name], inbound_args[destination_name] = (
                    args[destination_name],
                    args[origin_name],
                )
                self._set_arg(inbound_args, props, "date_forward", query.return_date)

                outbound_payload = mcp.call_tool(tool, outbound_args)
                inbound_payload = mcp.call_tool(tool, inbound_args)
                raw_artifacts.extend(
                    [
                        RawArtifact(
                            payload=outbound_payload,
                            endpoint=f"{self.endpoint}#{tool}:outbound",
                            request_params=outbound_args,
                        ),
                        RawArtifact(
                            payload=inbound_payload,
                            endpoint=f"{self.endpoint}#{tool}:inbound",
                            request_params=inbound_args,
                        ),
                    ]
                )
                offers = self._combine_one_way(
                    outbound_payload, inbound_payload, query, offer_type
                )

            return ConnectorResult(
                source_code=self.code,
                offer_type=offer_type,
                outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
                offers=offers,
                raw_artifacts=raw_artifacts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                connector_version=self.version,
                diagnostics={"tool": tool, "mapped_args": sorted(args)},
            )

    def _parse_transport(
        self,
        payload: Any,
        query,  # noqa: ANN001
        offer_type: OfferType,
        *,
        round_trip: bool,
    ) -> list[Any]:
        offers: list[Any] = []
        for item in _extract_offer_list(payload):
            offer = _unwrap_offer(item)
            legs = offer.get("legs")
            legs = [leg for leg in legs if isinstance(leg, dict)] if isinstance(legs, list) else []
            outbound = _segments(legs[0]) if legs else []
            inbound = _segments(legs[1]) if len(legs) > 1 else []

            price = to_decimal(_get_path(offer, "best_offer", "price", "total"))
            currency = _get_path(offer, "best_offer", "price", "currency", default="RUB")

            if offer_type == OfferType.FLIGHT:
                offers.append(
                    ProviderFlightOffer(
                        source_offer_id=_first_str(offer, "id", "offer_id"),
                        currency=str(currency or "RUB"),
                        total_price=price,
                        price_basis="ALL_PASSENGERS",
                        deeplink=_first_str(offer, "search_results_url"),
                        origin_code=_first_str(outbound[0].get("from") or {}, "iata_code", "code")
                        if outbound
                        else None,
                        destination_code=_first_str(
                            outbound[-1].get("to") or {}, "iata_code", "code"
                        )
                        if outbound
                        else None,
                        outbound_segments=[_build_segment(seg) for seg in outbound],
                        inbound_segments=[_build_segment(seg) for seg in inbound],
                        outbound_duration_minutes=_as_int(legs[0].get("duration")) if legs else None,
                        inbound_duration_minutes=_as_int(legs[1].get("duration"))
                        if len(legs) > 1
                        else None,
                        cabin_class=_first_str(offer.get("best_offer") or {}, "service_class"),
                        fare_family=_get_path(offer, "best_offer", "price", "fare_family"),
                        baggage_raw=_stringify(_get_path(offer, "best_offer", "price", "baggage")),
                        refund_raw=_stringify(_get_path(offer, "best_offer", "price", "refund")),
                        fare_conditions_raw=_stringify(
                            _get_path(offer, "best_offer", "price", "fare_conditions")
                        ),
                        passenger_count=query.traveler_count,
                        is_round_trip=round_trip and bool(inbound),
                        source_payload={"status": offer.get("status")},
                    )
                )
            else:
                offers.append(
                    ProviderRailOffer(
                        source_offer_id=_first_str(offer, "id", "offer_id"),
                        currency=str(currency or "RUB"),
                        total_price=price,
                        # Туту отдает стоимость билета; пересчет на пассажиров
                        # выполняет нормализация.
                        price_basis="PER_PASSENGER",
                        outbound_segments=[_build_segment(seg) for seg in outbound],
                        inbound_segments=[_build_segment(seg) for seg in inbound],
                        outbound_train_number=_first_str(outbound[0], "train_number", "voyage_no")
                        if outbound
                        else None,
                        inbound_train_number=_first_str(inbound[0], "train_number", "voyage_no")
                        if inbound
                        else None,
                        origin_station_name=_first_str(outbound[0].get("from") or {}, "name")
                        if outbound
                        else None,
                        destination_station_name=_first_str(outbound[-1].get("to") or {}, "name")
                        if outbound
                        else None,
                        origin_city_name=_first_str(outbound[0].get("from") or {}, "city_name")
                        if outbound
                        else None,
                        destination_city_name=_first_str(outbound[-1].get("to") or {}, "city_name")
                        if outbound
                        else None,
                        car_type_raw=_first_str(offer.get("best_offer") or {}, "car_type"),
                        service_classes=[
                            item
                            for item in [_first_str(offer.get("best_offer") or {}, "service_class")]
                            if item
                        ],
                        carriers=[
                            seg.get("carrier_name")
                            for seg in outbound
                            if isinstance(seg.get("carrier_name"), str)
                        ],
                        price_per_place_outbound=price,
                        passenger_count=query.traveler_count,
                        is_round_trip=round_trip and bool(inbound),
                        source_payload={"status": offer.get("status")},
                    )
                )
        return offers

    def _combine_one_way(
        self,
        outbound_payload: Any,
        inbound_payload: Any,
        query,  # noqa: ANN001
        offer_type: OfferType,
    ) -> list[Any]:
        """Собирает round-trip из двух односторонних выдач.

        Комбинируются только сопоставимые варианты (для ЖД — одного типа
        вагона), число комбинаций ограничено, чтобы не раздувать выборку.
        """
        outbound = self._parse_transport(outbound_payload, query, offer_type, round_trip=False)
        inbound = self._parse_transport(inbound_payload, query, offer_type, round_trip=False)
        if not outbound or not inbound:
            return []

        limit = int(self.context.config.get("roundtrip_legs_per_direction", 8))
        outbound = sorted(outbound, key=lambda o: o.total_price or 0)[:limit]
        inbound = sorted(inbound, key=lambda o: o.total_price or 0)[:limit]

        combined: list[Any] = []
        for out in outbound:
            for back in inbound:
                if offer_type == OfferType.RAIL and (out.car_type_raw != back.car_type_raw):
                    continue
                merged = out.model_copy(deep=True)
                merged.inbound_segments = back.outbound_segments
                merged.is_round_trip = True
                merged.source_offer_id = f"{out.source_offer_id}|{back.source_offer_id}"
                if offer_type == OfferType.RAIL:
                    merged.inbound_train_number = back.outbound_train_number
                    merged.price_per_place_inbound = back.price_per_place_outbound
                    merged.total_price = _sum_prices(out.total_price, back.total_price)
                    merged.inbound_duration_minutes = back.outbound_duration_minutes
                else:
                    merged.total_price = _sum_prices(out.total_price, back.total_price)
                    merged.inbound_duration_minutes = back.outbound_duration_minutes
                combined.append(merged)
        return combined

    # ------------------------------------------------------------------ #
    # Сбор проживания
    # ------------------------------------------------------------------ #

    def collect_accommodation(self, query: AccommodationQuery) -> ConnectorResult:
        started = time.perf_counter()
        with self._http() as http:
            mcp = McpClient(http, self.endpoint, client_name="travel-cost-observatory")
            self._load_tool_schemas(mcp)
            if self.TOOL_HOTELS not in self._tool_schemas:
                raise ConnectorSchemaError(
                    f"MCP-сервер не предоставляет инструмент {self.TOOL_HOTELS}",
                    source_code=self.code,
                )

            props = self._properties(self.TOOL_HOTELS)
            args: dict[str, Any] = {}
            city_value = self._city_argument(
                mcp, props, "city", query.city_name, query.city_source_ids
            )
            if self._set_arg(args, props, "city", city_value) is None:
                raise ConnectorSchemaError(
                    f"Не удалось сопоставить город со схемой {self.TOOL_HOTELS}: "
                    f"доступные аргументы {sorted(props)}",
                    source_code=self.code,
                )
            self._set_arg(args, props, "check_in", query.check_in)
            self._set_arg(args, props, "check_out", query.check_out)
            self._set_arg(args, props, "adults", query.adults)
            if query.children_ages:
                self._set_arg(args, props, "children_ages", list(query.children_ages))
            stars_value = _stars_numeric(query.stars)
            if stars_value is not None:
                self._set_arg(args, props, "stars_min", stars_value)
                self._set_arg(args, props, "stars_max", stars_value)
            self._set_arg(args, props, "per_page", int(self.context.config.get("per_page", 50)))

            payload = mcp.call_tool(self.TOOL_HOTELS, args)
            offers = self._parse_hotels(payload, query)

            return ConnectorResult(
                source_code=self.code,
                offer_type=OfferType.ACCOMMODATION,
                outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
                offers=offers,
                raw_artifacts=[
                    RawArtifact(
                        payload=payload,
                        endpoint=f"{self.endpoint}#{self.TOOL_HOTELS}",
                        request_params=args,
                    )
                ],
                latency_ms=int((time.perf_counter() - started) * 1000),
                connector_version=self.version,
                diagnostics={"tool": self.TOOL_HOTELS, "mapped_args": sorted(args)},
            )

    def _parse_hotels(self, payload: Any, query: AccommodationQuery) -> list[ProviderAccommodationOffer]:
        offers: list[ProviderAccommodationOffer] = []
        for item in _extract_offer_list(payload):
            offer = _unwrap_offer(item)
            hotel = offer.get("hotel") if isinstance(offer.get("hotel"), dict) else {}
            best = offer.get("best_offer") if isinstance(offer.get("best_offer"), dict) else {}
            price = to_decimal(_get_path(best, "price", "total"))
            stars_raw = hotel.get("stars")
            stars = _as_int(stars_raw)
            offers.append(
                ProviderAccommodationOffer(
                    source_offer_id=_first_str(offer, "id", "offer_id"),
                    property_source_id=_first_str(hotel, "geo_id", "id", "alias"),
                    property_name=_first_str(hotel, "name"),
                    accommodation_type_raw=_first_str(hotel, "type", "kind", "property_type"),
                    stars=stars if stars and 1 <= stars <= 5 else None,
                    stars_unrated=stars_raw is not None and (stars == 0),
                    address=_first_str(hotel, "address"),
                    city_name=_first_str(hotel, "city_name") or query.city_name,
                    currency=str(_get_path(best, "price", "currency", default="RUB") or "RUB"),
                    total_price=price,
                    price_per_night=to_decimal(_get_path(best, "price", "per_night")),
                    price_basis="PER_ROOM_TOTAL",
                    check_in=parse_date(best.get("check_in")) or query.check_in,
                    check_out=parse_date(best.get("check_out")) or query.check_out,
                    nights=_as_int(best.get("nights")) or query.nights,
                    room_name=_first_str(best, "room_name"),
                    max_guests=_as_int(best.get("max_guests")),
                    # Поиск выполнялся по составу гостей, поэтому выдача
                    # вместимость подтверждает.
                    capacity_confirmed_by_query=True,
                    meal_raw=_first_str(best, "board_name", "meal", "board"),
                    cancellation_raw=_first_str(best, "cancellation", "refund_type", "cancel_policy"),
                    review_score=_as_float(best.get("review_score")),
                    review_count=_as_int(best.get("review_count")),
                    amenities=[str(a) for a in (best.get("amenities") or []) if a][:40],
                    deeplink=_first_str(best, "checkout_url"),
                    source_payload={"status": offer.get("status")},
                )
            )
        return offers

    # ------------------------------------------------------------------ #
    # Health check
    # ------------------------------------------------------------------ #

    def health_check(self) -> ConnectorResult:
        started = time.perf_counter()
        try:
            with self._http() as http:
                mcp = McpClient(http, self.endpoint, client_name="travel-cost-observatory")
                tools = mcp.list_tools()
        except Exception as exc:  # noqa: BLE001 — health check не должен падать
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=OfferType.FLIGHT,
                outcome=ConnectorOutcome.TRANSPORT_ERROR,
                error_code=type(exc).__name__,
                error_message=str(exc),
                connector_version=self.version,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        names = sorted(str(tool.get("name")) for tool in tools if tool.get("name"))
        return ConnectorResult(
            source_code=self.code,
            offer_type=OfferType.FLIGHT,
            outcome=ConnectorOutcome.SUCCESS,
            latency_ms=int((time.perf_counter() - started) * 1000),
            connector_version=self.version,
            diagnostics={"tools": names, "tool_count": len(names)},
        )


# --------------------------------------------------------------------------- #
# Вспомогательные функции модуля
# --------------------------------------------------------------------------- #


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    import json

    return json.dumps(value, ensure_ascii=False)[:255]


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_prices(left: Any, right: Any) -> Any:
    left_dec, right_dec = to_decimal(left), to_decimal(right)
    if left_dec is None or right_dec is None:
        return left_dec if right_dec is None else right_dec
    return left_dec + right_dec


def _first_iata(source_ids: dict[str, Any]) -> str | None:
    value = source_ids.get("iata")
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value) if value else None


def _stars_numeric(stars: str) -> int | None:
    return int(stars) if stars.isdigit() else None


def _build_geo_index(payload: Any) -> dict[str, Any]:
    """Строит индекс «нормализованное имя города → id» из ресурса ``tutu://geo``."""
    index: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name") or node.get("title") or node.get("city_name")
            identifier = (
                node.get("id")
                or node.get("city_id")
                or node.get("geo_id")
                or node.get("geo_point_id")
            )
            if isinstance(name, str) and identifier is not None:
                index.setdefault(normalize_text(name), identifier)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return index
