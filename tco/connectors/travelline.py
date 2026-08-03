"""Коннектор TravelLine (Search API, размещение).

Требует OAuth2 client_credentials. Токен живет 15 минут и не обновляется
refresh-токеном, поэтому кэшируется в памяти воркера с запасом.

Важное ограничение честности: точная форма тела ``POST /v1/search`` и имена
полей в ответе фиксируются на этапе партнерского онбординга. Поэтому маппинг
полей вынесен в конфигурацию источника (``config.response_paths``), а значения
по умолчанию соответствуют документации TravelLine. Источник поставляется со
статусом ``CANDIDATE`` и должен быть переведен в ``APPROVED`` только после
прохождения Source Qualification с реальными учетными данными.
"""

from __future__ import annotations

import time
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory
from tco.core.errors import ConnectorAuthError
from tco.core.utils import to_decimal, utcnow
from tco.connectors.base import BaseConnector
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    ProviderAccommodationOffer,
    RawArtifact,
)
from tco.connectors.http import ResilientHttpClient

#: Пути к полям ответа. Переопределяются через ``Source.config.response_paths``.
DEFAULT_RESPONSE_PATHS: dict[str, str] = {
    "items": "properties",
    "property_id": "propertyId",
    "property_name": "name",
    "address": "address.fullAddress",
    "city": "address.city",
    "stars": "starRating",
    "kind": "propertyKind",
    "rate_plans": "ratePlans",
    "room_name": "roomTypeName",
    "price": "totalPrice.amount",
    "currency": "totalPrice.currency",
    "meal": "mealPlan.name",
    "cancellation": "cancellationPolicy.name",
    "occupancy": "occupancy.maxGuests",
}


def _dig(payload: Any, path: str, default: Any = None) -> Any:
    """Достает значение по пути вида ``a.b.c``."""
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return default
        if current is None:
            return default
    return current


class TravellineConnector(BaseConnector):
    """Коннектор размещения TravelLine."""

    code = "travelline"
    title = "TravelLine (Partner Search API)"
    category = SourceCategory.ACCOMMODATION
    supported_offer_types = (OfferType.ACCOMMODATION,)
    version = "1.0.0"
    requires_credentials = True
    default_allowed_hosts = ("partner.tlintegration.com",)

    def __init__(self, context) -> None:  # noqa: ANN001
        super().__init__(context)
        self.base_url = str(
            context.config.get("base_url") or "https://partner.tlintegration.com/api"
        ).rstrip("/")
        self.auth_url = str(
            context.config.get("auth_url") or "https://partner.tlintegration.com/auth/token"
        )
        self.client_id = context.credentials.get("client_id")
        self.client_secret = context.credentials.get("client_secret")
        self.response_paths = {
            **DEFAULT_RESPONSE_PATHS,
            **(context.config.get("response_paths") or {}),
        }
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def is_configured(self) -> tuple[bool, str | None]:
        missing = [
            name
            for name, value in (
                ("TRAVELLINE_CLIENT_ID", self.client_id),
                ("TRAVELLINE_CLIENT_SECRET", self.client_secret),
            )
            if not value
        ]
        if missing:
            return False, f"Не заданы учетные данные: {', '.join(missing)}"
        return True, None

    def _http(self, token: str | None = None) -> ResilientHttpClient:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return ResilientHttpClient(
            source_code=self.code,
            allowed_hosts=self.allowed_hosts,
            soft_timeout=self.context.soft_timeout,
            hard_timeout=self.context.hard_timeout,
            max_retries=self.context.max_retries,
            backoff_base=self.context.backoff_base,
            backoff_max=self.context.backoff_max,
            rate_limit_per_minute=self.context.rate_limit_per_minute,
            default_headers=headers,
        )

    def _fetch_token(self) -> str:
        """Получает access token (client_credentials). Токен живет 15 минут."""
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token

        with self._http() as http:
            response = http.post(
                self.auth_url,
                content="grant_type=client_credentials",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": _basic_auth(self.client_id or "", self.client_secret or ""),
                },
            )
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise ConnectorAuthError(
                "TravelLine не вернул access_token", source_code=self.code
            )
        expires_in = int(payload.get("expires_in", 900))
        # Запас 60 секунд, чтобы не попасть в момент истечения.
        self._token_expires_at = time.monotonic() + max(60, expires_in - 60)
        self._token = str(token)
        return self._token

    def collect_accommodation(self, query: AccommodationQuery) -> ConnectorResult:
        started = time.perf_counter()
        token = self._fetch_token()

        body: dict[str, Any] = {
            "arrivalDate": query.check_in.isoformat(),
            "departureDate": query.check_out.isoformat(),
            "adults": query.adults,
            "childAges": list(query.children_ages),
            "currencyCode": "RUB",
        }
        city_id = query.city_source_ids.get("city_id") or query.city_source_ids.get("region_id")
        if city_id is not None:
            body["cityId"] = city_id
        else:
            body["city"] = query.city_name

        with self._http(token) as http:
            response = http.post(f"{self.base_url}/v1/search", json_body=body)
            payload = response.json() if response.content else {}

        offers = self._parse(payload, query)
        return ConnectorResult(
            source_code=self.code,
            offer_type=OfferType.ACCOMMODATION,
            outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
            offers=offers,
            raw_artifacts=[
                RawArtifact(
                    payload=payload,
                    endpoint=f"{self.base_url}/v1/search",
                    request_params=body,
                    http_status=response.status_code,
                    latency_ms=response.latency_ms,
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
            connector_version=self.version,
        )

    def _parse(self, payload: Any, query: AccommodationQuery) -> list[ProviderAccommodationOffer]:
        paths = self.response_paths
        items = _dig(payload, paths["items"], default=None)
        if items is None and isinstance(payload, list):
            items = payload
        if not isinstance(items, list):
            return []

        offers: list[ProviderAccommodationOffer] = []
        nights = query.nights or 1
        for item in items:
            if not isinstance(item, dict):
                continue
            rate_plans = _dig(item, paths["rate_plans"], default=[])
            if not isinstance(rate_plans, list) or not rate_plans:
                rate_plans = [item]
            for plan in rate_plans:
                if not isinstance(plan, dict):
                    continue
                price = to_decimal(_dig(plan, paths["price"]))
                if price is None:
                    continue
                max_guests = _dig(plan, paths["occupancy"])
                offers.append(
                    ProviderAccommodationOffer(
                        source_offer_id=f"{_dig(item, paths['property_id'])}:{_dig(plan, paths['room_name'])}",
                        property_source_id=_as_str(_dig(item, paths["property_id"])),
                        property_name=_as_str(_dig(item, paths["property_name"])),
                        accommodation_type_raw=_as_str(_dig(item, paths["kind"])),
                        stars=_as_int(_dig(item, paths["stars"])),
                        address=_as_str(_dig(item, paths["address"])),
                        city_name=_as_str(_dig(item, paths["city"])) or query.city_name,
                        currency=str(_dig(plan, paths["currency"]) or "RUB"),
                        total_price=price,
                        price_basis="PER_ROOM_TOTAL",
                        price_per_night=price / nights if nights else None,
                        check_in=query.check_in,
                        check_out=query.check_out,
                        nights=nights,
                        room_name=_as_str(_dig(plan, paths["room_name"])),
                        max_guests=_as_int(max_guests),
                        capacity_confirmed_by_query=True,
                        meal_raw=_as_str(_dig(plan, paths["meal"])),
                        cancellation_raw=_as_str(_dig(plan, paths["cancellation"])),
                    )
                )
        return offers

    def health_check(self) -> ConnectorResult:
        started = time.perf_counter()
        configured, reason = self.is_configured()
        if not configured:
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=OfferType.ACCOMMODATION,
                outcome=ConnectorOutcome.DISABLED,
                error_code="NOT_CONFIGURED",
                error_message=reason or "",
                connector_version=self.version,
            )
        try:
            token = self._fetch_token()
            with self._http(token) as http:
                response = http.get(f"{self.base_url}/v1/properties", params={"count": 1})
        except Exception as exc:  # noqa: BLE001
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=OfferType.ACCOMMODATION,
                outcome=ConnectorOutcome.TRANSPORT_ERROR,
                error_code=type(exc).__name__,
                error_message=str(exc),
                connector_version=self.version,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return ConnectorResult(
            source_code=self.code,
            offer_type=OfferType.ACCOMMODATION,
            outcome=ConnectorOutcome.SUCCESS,
            http_status=response.status_code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            connector_version=self.version,
        )


def _basic_auth(client_id: str, client_secret: str) -> str:
    import base64

    token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {token}"


def _as_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
