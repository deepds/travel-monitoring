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

import re
import time
from datetime import date
from decimal import Decimal
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory
from tco.core.errors import ConnectorError, ConnectorSchemaError
from tco.core.logging import get_logger
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

logger = get_logger(__name__)

#: Верхняя граница ``max_cars`` карты мест — жестко задана сервером Туту.
_SEATMAP_MAX_CARS = 20
#: Сколько поездов детализируется за один сбор (оба направления вместе).
_SEATMAP_DEFAULT_CALLS = 16

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
    "stars": ("stars", "stars_min", "min_stars", "stars_from"),
    "stars_max": ("stars_max", "max_stars", "stars_to"),
    "meals": ("meals", "meal_type", "board"),
    "breakfast_included": ("breakfast_included", "with_breakfast"),
    "free_cancellation": ("free_cancellation", "refundable_only"),
    "per_page": ("per_page", "limit", "page_size", "count"),
    # ``offset`` в кандидатах не значится намеренно: это другая величина.
    # Номер страницы, отправленный как смещение, дал бы вторую позицию выдачи
    # вместо второй страницы — то есть тихо вернул бы почти те же объекты.
    "page": ("page", "page_number"),
    # Тип объекта размещения. Источник умеет отсекать апартаменты и хостелы на
    # своей стороне, и это дешевле, чем отбирать их у себя: выдача ограничена
    # тридцатью объектами на страницу, и каждый лишний объект вытесняет отель.
    "hotel_types": ("hotel_types", "property_types", "accommodation_types", "types"),
}

#: Ключи, под которыми в ответах встречается список предложений.
OFFER_LIST_KEYS = ("offers", "items", "results", "data", "variants", "hotels", "list")

#: Значения фильтра ``meals`` инструмента search_hotels. Завтрак задается
#: отдельным аргументом ``breakfast_included`` — это документированный ярлык.
TUTU_MEAL_FILTER: dict[str, str] = {
    "NO_MEALS": "nomeal",
    "HALF_BOARD": "halfboard",
    "FULL_BOARD": "fullboard",
    "ALL_INCLUSIVE": "allinclusive",
}

#: Питание, которое подтверждается самим фактом серверной фильтрации: источник
#: не возвращает ``meal_name``, но отбирает предложения по запрошенному плану.
MEAL_CONFIRMED_BY_QUERY: frozenset[str] = frozenset({"BREAKFAST", *TUTU_MEAL_FILTER})

#: Типы объектов размещения, составляющие массовый гостиничный сегмент.
#:
#: Апартаменты и хостелы исключены по прямому указанию руководителя: это другой
#: продукт с другой ценой (в казанской выдаче апартаменты идут по 12–14 тысяч за
#: ночь при медиане отелей около 7 тысяч). Фильтр отдается источнику, а не
#: применяется у себя: страница выдачи ограничена тридцатью объектами, и каждый
#: отсеянный потом апартамент — это вытесненный отель, которого мы уже не
#: увидим.
#:
#: Проверять состав выборки этим фильтром нельзя: тип объекта источник в ответе
#: не возвращает (поле пусто у всех 84 объектов казанской выдачи), поэтому
#: контроль идет по категории номера в ``tco/engine/selection.py``.
TUTU_MASS_MARKET_HOTEL_TYPES: tuple[str, ...] = ("hotel",)


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


def _array_item_type(prop: dict[str, Any]) -> str:
    """Тип элементов массива с учетом ``anyOf``/``oneOf``."""
    for variant in (prop, *(prop.get("anyOf") or []), *(prop.get("oneOf") or [])):
        if not isinstance(variant, dict) or variant.get("type") != "array":
            continue
        items = variant.get("items")
        if isinstance(items, dict) and items.get("type"):
            return str(items["type"])
    return "string"


def _coerce(value: Any, target_type: str, prop: dict[str, Any] | None = None) -> Any:
    if target_type == "array":
        # Схема может требовать список там, где логически передается одно
        # значение: ``stars`` у search_hotels объявлен как multi-select.
        # Скаляр, отправленный как есть, отклоняется валидацией источника.
        items = value if isinstance(value, (list, tuple)) else [value]
        item_type = _array_item_type(prop or {})
        return [_coerce(item, item_type) for item in items]
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
    #: 1.1.0 — выдача отелей и авиа дочитывается постранично, апартаменты и
    #: хостелы отсекаются на стороне источника. Версия поднята потому, что
    #: изменился состав выборки, а не только код: сравнивать наблюдения 1.0.0 и
    #: 1.1.0 напрямую нельзя.
    version = "1.1.0"
    requires_credentials = False
    default_allowed_hosts = ("mcp.tutu.ru",)

    TOOL_AVIA = "search_avia"
    TOOL_RAIL = "search_rail"
    TOOL_HOTELS = "search_hotels"
    #: Карта мест — единственный источник цены по типу вагона.
    #: Поиск отдает только «цену от» по всем классам сразу, а ``get_offer_details``
    #: обрезает список тарифов двадцатью строками, и на поезде с большим числом
    #: плацкартных тарифов купе в него не попадает.
    TOOL_RAIL_SEATMAP = "get_rail_seatmap"

    def __init__(self, context) -> None:  # noqa: ANN001
        super().__init__(context)
        self.endpoint = str(context.config.get("endpoint") or "https://mcp.tutu.ru/mcp")
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._geo_index: dict[str, Any] | None = None
        #: Остаток бюджета вызовов карты мест на текущий сбор.
        self._seatmap_budget = 0

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
        prop = props.get(name) or {}
        coerced = _coerce(value, _prop_type(props, name), prop)
        clamped, note = _clamp_to_schema(coerced, prop)
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

    def _fetch_pages(
        self,
        mcp: McpClient,
        tool: str,
        args: dict[str, Any],
        props: dict[str, Any],
        *,
        paginate: bool,
    ) -> tuple[list[tuple[dict[str, Any], Any]], dict[str, Any]]:
        """Дочитывает выдачу инструмента постранично.

        Поиск отдает не больше ``page_size`` объектов за раз (максимум схемы —
        30), сортировки у него нет, и какие именно тридцать попадут в ответ,
        решает источник. Пока читалась одна страница, выборка систематически
        обрезалась: 95 % запросов по отелям и 90 % по авиа упирались в потолок.
        По Казани это давало медиану 7 984 рубля вместо 6 923 по всем 84
        объектам — то самое расхождение с сайтом, ради которого страница и
        дочитывается.

        По ЖД пагинация не нужна и не включается: на маршруте ходит три-пять
        поездов, до потолка далеко (4,9 предложения на запрос у Туту, 2,6 у
        РЖД), и лишние обращения там ничего не дадут.

        Обход прекращается, когда страница пришла неполной или пустой — это
        конец выдачи, — либо когда исчерпан бюджет времени сбора. Собранное к
        этому моменту сохраняется, а результат помечается частичным: выборка
        настоящая, но неполная, и об этом должно быть видно.
        """
        page_arg = _pick_arg(props, "page") if paginate else None
        per_page_arg = _pick_arg(props, "per_page")
        page_size = args.get(per_page_arg) if per_page_arg else None
        limit = max(1, int(self.context.max_pages)) if page_arg else 1

        pages: list[tuple[dict[str, Any], Any]] = []
        stopped = "page_limit"
        for number in range(1, limit + 1):
            call_args = dict(args)
            if page_arg is not None and number > 1:
                self._set_arg(call_args, props, "page", number)
            payload = mcp.call_tool(tool, call_args)
            pages.append((call_args, payload))

            count = len(_extract_offer_list(payload))
            if count == 0:
                stopped = "empty_page"
                break
            # Неполная страница означает конец выдачи: следующая была бы пустой.
            if isinstance(page_size, int) and count < page_size:
                stopped = "last_page"
                break
            if self.context.budget_exhausted:
                stopped = "time_budget"
                break

        diagnostics = {
            "pages_fetched": len(pages),
            "pages_limit": limit,
            "stopped_because": stopped,
            # Обход прерван, а не завершен: за границей осталось неизвестно
            # сколько объектов, и медиана по такой выборке смещена.
            "truncated": stopped in ("page_limit", "time_budget") and len(pages) >= 1,
        }
        return pages, diagnostics

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

        # Карта мест запрашивается отдельным вызовом на каждый поезд, поэтому
        # бюджет ограничен и общий на оба направления поездки.
        self._seatmap_budget = (
            0
            if is_avia
            else int(self.context.config.get("rail_seatmap_calls", _SEATMAP_DEFAULT_CALLS))
        )

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
                    _avia_place_code(query.origin_source_ids, query.origin_city_code)
                    if is_avia
                    else None,
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
                    _avia_place_code(query.destination_source_ids, query.destination_city_code)
                    if is_avia
                    else None,
                ),
            )
            if not mapped_origin or not mapped_destination:
                raise ConnectorSchemaError(
                    f"Не удалось сопоставить параметры маршрута со схемой {tool}: "
                    f"доступные аргументы {sorted(props)}",
                    source_code=self.code,
                )

            self._set_arg(args, props, "date_forward", query.departure_date)
            # Обратная дата не передается у односторонней поездки: там обратного
            # плеча нет вовсе, и круговой поиск вернул бы не то, что просили.
            has_return_arg = (
                not query.is_one_way
                and self._set_arg(args, props, "date_back", query.return_date) is not None
            )
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
            # Дочитывается только авиа: у ЖД на маршруте три-пять поездов, и
            # выдача до потолка страницы не доходит никогда.
            paginate = is_avia

            if has_return_arg:
                pages, page_stats = self._fetch_pages(
                    mcp, tool, args, props, paginate=paginate
                )
                for call_args, payload in pages:
                    raw_artifacts.append(
                        RawArtifact(
                            payload=payload,
                            endpoint=f"{self.endpoint}#{tool}",
                            request_params=call_args,
                            content_type="application/json",
                        )
                    )
                    offers.extend(
                        self._parse_transport(
                            payload, query, offer_type, round_trip=True, mcp=mcp
                        )
                    )
                truncated = bool(page_stats["truncated"])
            elif query.is_one_way:
                # Поездка в одну сторону: один поиск, обратного плеча нет.
                # Вдвое дешевле кругового сбора по обращениям, а у ЖД еще и по
                # картам мест — их бюджет тратится только на плечо «туда».
                pages, page_stats = self._fetch_pages(
                    mcp, tool, args, props, paginate=paginate
                )
                for call_args, payload in pages:
                    raw_artifacts.append(
                        RawArtifact(
                            payload=payload,
                            endpoint=f"{self.endpoint}#{tool}:outbound",
                            request_params=call_args,
                        )
                    )
                    offers.extend(
                        self._parse_transport(
                            payload, query, offer_type, round_trip=False, mcp=mcp
                        )
                    )
                truncated = bool(page_stats["truncated"])
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

                outbound_pages, outbound_stats = self._fetch_pages(
                    mcp, tool, outbound_args, props, paginate=paginate
                )
                inbound_pages, inbound_stats = self._fetch_pages(
                    mcp, tool, inbound_args, props, paginate=paginate
                )
                for label, collected in (
                    ("outbound", outbound_pages),
                    ("inbound", inbound_pages),
                ):
                    raw_artifacts.extend(
                        RawArtifact(
                            payload=payload,
                            endpoint=f"{self.endpoint}#{tool}:{label}",
                            request_params=call_args,
                        )
                        for call_args, payload in collected
                    )
                offers = self._combine_one_way(
                    [payload for _, payload in outbound_pages],
                    [payload for _, payload in inbound_pages],
                    query,
                    offer_type,
                    mcp=mcp,
                )
                page_stats = {"outbound": outbound_stats, "inbound": inbound_stats}
                truncated = bool(outbound_stats["truncated"] or inbound_stats["truncated"])

            return ConnectorResult(
                source_code=self.code,
                offer_type=offer_type,
                outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
                offers=offers,
                raw_artifacts=raw_artifacts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                connector_version=self.version,
                is_partial=truncated,
                diagnostics={
                    "tool": tool,
                    "mapped_args": sorted(args),
                    "pagination": page_stats,
                },
            )

    def _rail_class_prices(self, mcp: McpClient, offer: dict[str, Any]) -> dict[str, Decimal]:
        """Цена за место по каждому типу вагона поезда.

        Возвращает пустой словарь, если карта мест недоступна: перевозчик может
        ее не отдавать (``no_layout_for_carrier``), и это не ошибка сбора.

        Цены карты мест — предкорзинные: Туту предупреждает, что финальная
        корзина выше на 6–8 % за счет его сервисного сбора. Здесь они берутся
        как есть, без домножения на коэффициент: подгонять чужую цену
        собственной поправкой значило бы выдавать оценку за наблюдение.
        """
        details_ref = offer.get("details_ref")
        if not isinstance(details_ref, dict) or self._seatmap_budget <= 0:
            return {}
        # Перебор карт мест — самая длинная часть сбора ЖД: до 16 обращений
        # примерно по 8,5 секунды. Когда бюджет времени вышел, он прекращается,
        # а поезда остаются в выборке с ценой «от» из поиска: неполные данные
        # лучше, чем снятая по таймауту задача, которая теряет все.
        if self.context.budget_exhausted:
            self.log.info("Карта мест пропущена: исчерпан бюджет времени сбора")
            return {}
        if self.TOOL_RAIL_SEATMAP not in self._tool_schemas:
            return {}

        self._seatmap_budget -= 1
        try:
            payload = mcp.call_tool(
                self.TOOL_RAIL_SEATMAP,
                {
                    "details_ref": details_ref,
                    "view": "compact",
                    "max_cars": _SEATMAP_MAX_CARS,
                    # Сами места не нужны — нужны только группы с ценами.
                    "max_seats_per_car": 1,
                },
            )
        except ConnectorError as exc:
            logger.debug("Карта мест недоступна", source=self.code, error=str(exc))
            return {}

        return _seatmap_class_prices(payload)

    def _parse_transport(
        self,
        payload: Any,
        query,  # noqa: ANN001
        offer_type: OfferType,
        *,
        round_trip: bool,
        mcp: McpClient | None = None,
    ) -> list[Any]:
        """Разбирает выдачу поиска транспорта.

        Фактический контракт проверен на живом сервисе и отличается от
        публичного описания: цена лежит в ``price.amount``, плечи — в ``legs``
        с меткой ``outbound``/``inbound``, а пункты отправления и прибытия
        приходят строками вида «Москва — Внуково (VKO)», а не объектами.

        Каждый тарифный вариант (``variants``) становится отдельным
        предложением: у вариантов разные цена и условия по багажу, и
        схлопывание их в один объект потеряло бы именно ту информацию, по
        которой методика различает авиатарифы.
        """
        offers: list[Any] = []
        for item in _extract_offer_list(payload):
            offer = _unwrap_offer(item)
            outbound_raw, inbound_raw = _split_legs(offer)
            outbound = [_segment_from_leg(seg) for seg in outbound_raw]
            inbound = [_segment_from_leg(seg) for seg in inbound_raw]
            base_price = to_decimal(_get_path(offer, "price", "amount"))
            currency = str(_get_path(offer, "price", "currency", default="RUB") or "RUB")
            offer_id = _first_str(offer, "offer_id", "id")
            deeplink = _first_str(offer, "checkout_url", "search_results_url")
            variants = [v for v in (offer.get("variants") or []) if isinstance(v, dict)]

            if offer_type == OfferType.FLIGHT:
                for index, variant in enumerate(variants or [{}]):
                    conditions = variant.get("conditions") or {}
                    price = to_decimal(_get_path(variant, "price", "amount")) or base_price
                    offers.append(
                        ProviderFlightOffer(
                            source_offer_id=f"{offer_id}:{index}" if variants else offer_id,
                            currency=str(
                                _get_path(variant, "price", "currency", default=currency)
                                or currency
                            ),
                            total_price=price,
                            price_basis="ALL_PASSENGERS",
                            deeplink=deeplink,
                            origin_code=outbound[0].origin_code if outbound else None,
                            destination_code=outbound[-1].destination_code if outbound else None,
                            origin_name=query.origin_city_name,
                            destination_name=query.destination_city_name,
                            outbound_segments=outbound,
                            inbound_segments=inbound,
                            outbound_duration_minutes=_leg_duration(outbound_raw),
                            inbound_duration_minutes=_leg_duration(inbound_raw),
                            cabin_class=_first_str(variant, "service_class")
                            or _first_str(offer, "service_class"),
                            fare_family=_first_str(conditions, "fare_family"),
                            baggage_raw=_baggage_text(conditions),
                            refund_raw=_refund_text(conditions),
                            passenger_count=query.traveler_count,
                            is_round_trip=bool(offer.get("is_round_trip")) and bool(inbound),
                            source_payload={"transport": offer.get("transport")},
                        )
                    )
            else:
                # Поиск отдает одну строку на поезд с «ценой от» по всем классам
                # сразу. Для сопоставления с РЖД нужна цена конкретного типа
                # вагона, поэтому поезд разворачивается в предложение на класс.
                class_prices = self._rail_class_prices(mcp, offer) if mcp is not None else {}
                fallback_type = _rail_car_type(offer, variants)
                priced: list[tuple[str | None, Decimal | None, str]] = (
                    [(car_type, price, "seatmap") for car_type, price in class_prices.items()]
                    if class_prices
                    # Без карты мест остается прежнее поведение: одна строка на
                    # поезд. Класс не определен, поэтому нормализация пометит ее
                    # как неклассифицированную, а не подставит чужой.
                    else [(fallback_type, base_price, "search")]
                )

                common = dict(
                    currency=currency,
                    price_basis="PER_PASSENGER",
                    outbound_segments=outbound,
                    inbound_segments=inbound,
                    outbound_train_number=outbound[0].vehicle_number if outbound else None,
                    inbound_train_number=inbound[0].vehicle_number if inbound else None,
                    origin_station_name=outbound[0].origin_name if outbound else None,
                    destination_station_name=outbound[-1].destination_name if outbound else None,
                    origin_city_name=query.origin_city_name,
                    destination_city_name=query.destination_city_name,
                    outbound_duration_minutes=_leg_duration(outbound_raw),
                    inbound_duration_minutes=_leg_duration(inbound_raw),
                    carriers=[str(c) for c in (offer.get("carriers") or []) if c],
                    deeplink=deeplink,
                    passenger_count=query.traveler_count,
                    is_round_trip=round_trip and bool(inbound),
                )

                for car_type, price, origin_of_price in priced:
                    offers.append(
                        ProviderRailOffer(
                            source_offer_id=(
                                f"{offer_id}:{car_type}" if car_type else offer_id
                            ),
                            total_price=price,
                            price_per_place_outbound=price,
                            car_type_raw=car_type,
                            source_payload={
                                "transport": offer.get("transport"),
                                # Провенанс цены: по карте мест она предкорзинная
                                # и ниже финальной корзины на 6–8 %.
                                "price_source": origin_of_price,
                                "search_price_from": float(base_price)
                                if base_price is not None
                                else None,
                            },
                            **common,
                        )
                    )
        return offers

    def _combine_one_way(
        self,
        outbound_payloads: list[Any],
        inbound_payloads: list[Any],
        query,  # noqa: ANN001
        offer_type: OfferType,
        mcp: McpClient | None = None,
    ) -> list[Any]:
        """Собирает round-trip из двух односторонних выдач.

        Каждое плечо может прийти несколькими страницами — они разбираются
        подряд и складываются в одну выборку до комбинирования.

        Комбинируются только сопоставимые варианты (для ЖД — одного типа
        вагона), число комбинаций ограничено, чтобы не раздувать выборку.
        """
        outbound: list[Any] = []
        for payload in outbound_payloads:
            outbound.extend(
                self._parse_transport(payload, query, offer_type, round_trip=False, mcp=mcp)
            )
        inbound: list[Any] = []
        for payload in inbound_payloads:
            inbound.extend(
                self._parse_transport(payload, query, offer_type, round_trip=False, mcp=mcp)
            )
        if not outbound or not inbound:
            return []

        limit = int(self.context.config.get("roundtrip_legs_per_direction", 8))
        outbound = _cheapest_per_class(outbound, offer_type, limit)
        inbound = _cheapest_per_class(inbound, offer_type, limit)

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
            # Фильтры отдаются источнику, а не применяются только локально:
            # иначе выдача из 30 объектов почти целиком отсеивается профилем,
            # и выборка для агрегации становится непригодно малой.
            stars_value = _stars_numeric(query.stars)
            if stars_value is not None:
                # Тип приводится по схеме: source ожидает список категорий.
                self._set_arg(args, props, "stars", stars_value)
            if query.meal_type == "BREAKFAST":
                self._set_arg(args, props, "breakfast_included", True)
            elif query.meal_type in TUTU_MEAL_FILTER:
                self._set_arg(args, props, "meals", [TUTU_MEAL_FILTER[query.meal_type]])
            if query.cancellation_filter == "FREE_CANCELLATION":
                self._set_arg(args, props, "free_cancellation", True)
            # Массовый сегмент — гостиницы. Апартаменты и хостелы отсекаются на
            # стороне источника, чтобы не занимать ими страницу выдачи.
            hotel_types = self.context.config.get(
                "hotel_types", list(TUTU_MASS_MARKET_HOTEL_TYPES)
            )
            if hotel_types:
                self._set_arg(args, props, "hotel_types", list(hotel_types))
            self._set_arg(args, props, "per_page", int(self.context.config.get("per_page", 50)))

            pages, page_stats = self._fetch_pages(
                mcp, self.TOOL_HOTELS, args, props, paginate=True
            )
            offers: list[ProviderAccommodationOffer] = []
            raw_artifacts: list[RawArtifact] = []
            for call_args, payload in pages:
                raw_artifacts.append(
                    RawArtifact(
                        payload=payload,
                        endpoint=f"{self.endpoint}#{self.TOOL_HOTELS}",
                        request_params=call_args,
                    )
                )
                offers.extend(self._parse_hotels(payload, query))

            return ConnectorResult(
                source_code=self.code,
                offer_type=OfferType.ACCOMMODATION,
                outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
                offers=offers,
                raw_artifacts=raw_artifacts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                connector_version=self.version,
                is_partial=bool(page_stats["truncated"]),
                diagnostics={
                    "tool": self.TOOL_HOTELS,
                    "mapped_args": sorted(args),
                    "pagination": page_stats,
                },
            )

    def _parse_hotels(
        self, payload: Any, query: AccommodationQuery
    ) -> list[ProviderAccommodationOffer]:
        """Разбирает выдачу поиска отелей.

        Фактический контракт: объекты лежат в корневом ``hotels``, период
        размещения — в ``stay``, цена и условия — в ``best_offer`` с явным
        признаком базы цены ``price_basis``.
        """
        if not isinstance(payload, dict):
            return []
        stay = payload.get("stay") if isinstance(payload.get("stay"), dict) else {}
        nights = _as_int(stay.get("nights")) or query.nights or 1
        check_in = parse_date(stay.get("check_in")) or query.check_in
        check_out = parse_date(stay.get("check_out")) or query.check_out
        # Ссылка на ту же выдачу у источника — общая для страницы. По ней цену
        # проверяют руками за полминуты; без нее сверка с сайтом упирается в
        # ручной подбор параметров поиска.
        search_url = _first_str(payload, "search_results_url", "search_url")

        offers: list[ProviderAccommodationOffer] = []
        for hotel in _extract_offer_list(payload):
            best = hotel.get("best_offer") if isinstance(hotel.get("best_offer"), dict) else {}
            price = to_decimal(_get_path(best, "price", "amount"))
            if price is None:
                continue
            # ``stay_total`` — цена за весь период, что и требует методика.
            basis = str(best.get("price_basis") or "stay_total")
            total = price if basis == "stay_total" else price * nights
            location = hotel.get("location") if isinstance(hotel.get("location"), dict) else {}
            stars = _as_int(hotel.get("stars"))

            offers.append(
                ProviderAccommodationOffer(
                    source_offer_id=_first_str(hotel, "tutu_offer_id", "hotel_id"),
                    property_source_id=_first_str(hotel, "hotel_id", "hotel_geo_id", "alias"),
                    property_name=_first_str(hotel, "name"),
                    accommodation_type_raw=_first_str(hotel, "type", "kind")
                    or query.accommodation_type,
                    stars=stars if stars and 1 <= stars <= 5 else None,
                    stars_unrated=stars == 0,
                    address=_first_str(hotel, "address"),
                    city_name=_first_str(location, "city") or query.city_name,
                    latitude=_as_float(location.get("lat")),
                    longitude=_as_float(location.get("lng")),
                    currency=str(_get_path(best, "price", "currency", default="RUB") or "RUB"),
                    total_price=total,
                    price_basis="PER_ROOM_TOTAL",
                    price_per_night=(total / nights) if nights else None,
                    check_in=check_in,
                    check_out=check_out,
                    nights=nights,
                    room_name=_first_str(best, "room_name"),
                    # Поиск выполнялся по составу гостей, поэтому выдача
                    # подтверждает вместимость.
                    capacity_confirmed_by_query=True,
                    meal_raw=_meal_text(best, query.meal_type),
                    cancellation_raw=_cancellation_text(best),
                    review_score=_as_float(hotel.get("rating")),
                    review_count=_as_int(hotel.get("review_count")),
                    deeplink=(
                        _first_str(best, "checkout_url")
                        or _first_str(hotel, "checkout_url")
                        or search_url
                    ),
                    source_payload={
                        "alias": hotel.get("alias"),
                        "search_results_url": search_url,
                    },
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


#: «Москва — Внуково (VKO)» → («Москва — Внуково», «VKO»).
_PLACE_CODE_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<code>[^)]+)\)\s*$")


def _split_place(value: Any) -> tuple[str | None, str | None]:
    """Разбирает пункт маршрута.

    Источник отдает его строкой, а не объектом: «Москва — Внуково (VKO)»
    для авиа и «Москва — Ленинградский вокзал (2006004)» для ЖД.
    """
    if isinstance(value, dict):
        return (
            _first_str(value, "name", "title"),
            _first_str(value, "iata_code", "code", "station_code"),
        )
    if not isinstance(value, str) or not value.strip():
        return None, None
    match = _PLACE_CODE_RE.match(value.strip())
    if match:
        return match.group("name").strip(), match.group("code").strip()
    # «Сочи, AER» — код после запятой.
    if "," in value:
        head, _, tail = value.rpartition(",")
        tail = tail.strip()
        if 2 <= len(tail) <= 8 and tail.replace("-", "").isalnum():
            return head.strip(), tail
    return value.strip(), None


#: Метки обратного плеча. Источник использует «return», а не «inbound»,
#: поэтому распознавание по одному лишь префиксу «in» теряло все обратные
#: сегменты и делало каждое круговое предложение несопоставимым со сценарием.
_INBOUND_LABELS = frozenset({"inbound", "return", "back", "backward", "homeward"})


def _split_legs(offer: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Делит сегменты на плечи «туда» и «обратно».

    Первично используется метка ``label``; если она незнакома, работает
    позиционное правило: первое плечо — «туда», последующие — «обратно».
    """
    legs = [leg for leg in (offer.get("legs") or []) if isinstance(leg, dict)]
    outbound: list[dict] = []
    inbound: list[dict] = []
    for index, leg in enumerate(legs):
        label = str(leg.get("label") or "").strip().lower()
        if label in _INBOUND_LABELS:
            is_inbound = True
        elif label:
            is_inbound = False
        else:
            is_inbound = index > 0
        segments = [s for s in (leg.get("segments") or []) if isinstance(s, dict)] or [leg]
        (inbound if is_inbound else outbound).extend(segments)
    return outbound, inbound


def _segment_from_leg(raw: dict[str, Any]) -> ProviderSegment:
    origin_name, origin_code = _split_place(raw.get("from"))
    destination_name, destination_code = _split_place(raw.get("to"))
    return ProviderSegment(
        origin_code=origin_code,
        origin_name=origin_name,
        destination_code=destination_code,
        destination_name=destination_name,
        departure_at=parse_datetime(raw.get("departure_at")),
        arrival_at=parse_datetime(raw.get("arrival_at")),
        duration_minutes=_as_int(raw.get("duration_min") or raw.get("duration")),
        carrier_name=_first_str(raw, "carrier", "carrier_name", "airline_name"),
        carrier_code=_first_str(raw, "carrier_code", "airline_code"),
        vehicle_number=_first_str(raw, "voyage_no", "flight_no", "train_number"),
        vehicle_name=_first_str(raw, "train_name", "vehicle_name"),
        aircraft=_first_str(raw, "aircraft"),
    )


def _leg_duration(segments: list[dict[str, Any]]) -> int | None:
    values = [_as_int(s.get("duration_min") or s.get("duration")) for s in segments]
    values = [v for v in values if v]
    return sum(values) if values else None


def _baggage_text(conditions: dict[str, Any]) -> str | None:
    """Собирает описание багажа из структурированных условий тарифа.

    Источник отдает багаж объектами ``{kg, pieces}`` — это надежнее строк,
    поэтому классификация получает однозначный текст, а не догадку.
    """
    if not isinstance(conditions, dict):
        return None
    checked = conditions.get("baggage") if isinstance(conditions.get("baggage"), dict) else {}
    cabin = (
        conditions.get("cabin_baggage")
        if isinstance(conditions.get("cabin_baggage"), dict)
        else {}
    )
    checked_pieces = _as_int(checked.get("pieces")) or 0
    checked_kg = _as_int(checked.get("kg")) or 0
    cabin_pieces = _as_int(cabin.get("pieces")) or 0
    cabin_kg = _as_int(cabin.get("kg")) or 0

    if checked_pieces > 0 or checked_kg > 0:
        return f"Багаж включен: {checked_pieces} место {checked_kg} кг"
    if cabin_pieces > 0 or cabin_kg > 0:
        return f"Без багажа, ручная кладь {cabin_kg} кг"
    if checked or cabin:
        # Объекты присутствуют, но пусты — багаж действительно не включен.
        return "Без багажа"
    return None


def _refund_text(conditions: dict[str, Any]) -> str | None:
    if not isinstance(conditions, dict) or "refundable" not in conditions:
        return None
    return "Возвратный" if conditions.get("refundable") else "Невозвратный"


def _meal_text(best: dict[str, Any], requested_meal: str | None = None) -> str | None:
    """Определяет питание предложения.

    Если поиск выполнялся с серверным фильтром по питанию, выдача его и
    подтверждает — так же, как поиск по составу гостей подтверждает
    вместимость. Без этого источник, не заполняющий ``meal_name``, давал бы
    сплошной ``UNKNOWN`` и предложения отсеивались бы фильтром профиля.

    Возвращается код ``MealType``: ``classify_meal`` распознает его напрямую,
    без разбора текста.
    """
    name = _first_str(best, "meal_name", "board_name")
    if name:
        return name
    included = best.get("breakfast_included")
    if included is True:
        return "BREAKFAST"
    if included is False:
        return "NO_MEALS"
    if requested_meal in MEAL_CONFIRMED_BY_QUERY:
        return requested_meal
    return None


def _cancellation_text(best: dict[str, Any]) -> str | None:
    free = best.get("free_cancellation")
    if free is True:
        return "Бесплатная отмена"
    if free is False:
        return "Невозвратный тариф"
    for item in best.get("highlights") or []:
        if isinstance(item, dict) and item.get("type") == "policies":
            return _first_str(item, "text")
    return None


def _seatmap_class_prices(payload: Any) -> dict[str, Decimal]:
    """Минимальная цена за место по каждому типу вагона из карты мест.

    Тип вагона Туту отдает уже нормализованным (``RESERVED_SEAT``,
    ``COMPARTMENT``, ``LUX``, …), поэтому сопоставление кодов класса
    обслуживания (``3Б``, ``2К``) здесь не требуется: по первому символу его
    все равно нельзя вывести — ``2В`` и ``2С`` это сидячие, а не купе.
    """
    node = payload
    if isinstance(node, list) and node:
        node = node[0]
    if isinstance(node, dict) and isinstance(node.get("body"), dict):
        node = node["body"]
    if not isinstance(node, dict):
        return {}
    if node.get("seatmap_status") not in (None, "ok"):
        return {}

    prices: dict[str, Decimal] = {}
    for car in node.get("cars") or []:
        if not isinstance(car, dict):
            continue
        car_type = car.get("car_type")
        if not car_type:
            continue
        for group in car.get("seat_groups") or []:
            if not isinstance(group, dict):
                continue
            fare = group.get("cheapest_fare")
            if not isinstance(fare, dict):
                continue
            price = to_decimal(_get_path(fare, "price", "amount"))
            if price is None:
                continue
            current = prices.get(str(car_type))
            if current is None or price < current:
                prices[str(car_type)] = price
    return prices


def _cheapest_per_class(offers: list[Any], offer_type: OfferType, limit: int) -> list[Any]:
    """Оставляет самые дешевые предложения, но по каждому классу отдельно.

    Общий лимит на направление вытеснил бы купе плацкартом: плацкарт всегда
    дешевле, и в отсечку по цене попадали бы только его варианты.
    """
    if offer_type != OfferType.RAIL:
        return sorted(offers, key=lambda o: o.total_price or 0)[:limit]

    by_class: dict[str | None, list[Any]] = {}
    for item in offers:
        by_class.setdefault(item.car_type_raw, []).append(item)

    kept: list[Any] = []
    for group in by_class.values():
        kept.extend(sorted(group, key=lambda o: o.total_price or 0)[:limit])
    return kept


def _rail_car_type(offer: dict[str, Any], variants: list[dict[str, Any]]) -> str | None:
    """Ищет тип вагона в предложении или его вариантах."""
    for source in (offer, *(variants or [])):
        if not isinstance(source, dict):
            continue
        value = _first_str(source, "car_type", "car_type_name", "coach_type")
        if value:
            return value
        conditions = source.get("conditions")
        if isinstance(conditions, dict):
            value = _first_str(conditions, "car_type", "car_type_name")
            if value:
                return value
    return None


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


def _avia_place_code(source_ids: dict[str, Any], city_code: str) -> str | None:
    """Код места для авиапоиска: город целиком, а не один из его аэропортов.

    У городов с несколькими аэропортами брать первый из списка нельзя. Туту
    понимает код аэропорта буквально и отбрасывает все рейсы в остальные: на
    Москве (``SVO``, ``DME``, ``VKO``, ``ZIA``) это отсекало 76 % предложений
    и всех перевозчиков, кроме Аэрофлота, — в ответе источника это видно как
    ``post_filter_dropped_wrong_airport``. Наблюдаемый минимум оказывался
    завышен на четверть: 67 046 ₽ вместо 53 332 ₽ на живой сверке с сайтом.

    Код города каталога — метрополийный код IATA (``MOW``, ``LED``), источник
    распознает его как город. Для городов с единственным аэропортом список из
    одного кода однозначен, и он же используется.
    """
    value = source_ids.get("iata")
    codes = [str(item) for item in value] if isinstance(value, list) else ([str(value)] if value else [])
    if len(codes) == 1:
        return codes[0]
    if len(codes) > 1:
        return city_code or codes[0]
    return city_code or None


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
