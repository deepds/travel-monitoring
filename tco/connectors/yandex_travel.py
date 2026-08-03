"""Коннектор Яндекс Путешествий (Travel Partners API, отели).

Требует OAuth-токен партнера. Без токена источник остается в состоянии
``DISABLED`` и не участвует в расчете — это штатное поведение, а не ошибка.

Особенность API: часть ответов приходит с ``complete: false`` и требует
повторного опроса (polling).
"""

from __future__ import annotations

import time
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory
from tco.core.utils import to_decimal
from tco.connectors.base import BaseConnector
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    ProviderAccommodationOffer,
    RawArtifact,
)
from tco.connectors.http import ResilientHttpClient

#: Тип питания Яндекса → сырое значение для нормализации.
MEAL_ID_MAP = {
    "RO": "NO_MEALS",
    "BB": "BREAKFAST",
    "HB": "HALF_BOARD",
    "FB": "FULL_BOARD",
    "AI": "ALL_INCLUSIVE",
}

#: Тип размещения методики → значение параметра ``accomm_type``.
ACCOMM_TYPE_PARAM = {
    "HOTEL": "hotel",
    "HOSTEL": "hostel",
    "APARTMENT": "apartment",
    "GUEST_HOUSE": "guest_house",
    "SANATORIUM": "sanatorium",
}

#: Обратный маппинг питания сценария в параметр запроса.
MEAL_PARAM = {"NO_MEALS": "RO", "BREAKFAST": "BB"}


class YandexTravelConnector(BaseConnector):
    """Коннектор проживания Яндекс Путешествий."""

    code = "yandex_travel"
    title = "Яндекс Путешествия (Travel Partners API)"
    category = SourceCategory.ACCOMMODATION
    supported_offer_types = (OfferType.ACCOMMODATION,)
    version = "1.0.0"
    requires_credentials = True
    default_allowed_hosts = ("whitelabel.travel.yandex-net.ru", "travel.yandex-net.ru")

    def __init__(self, context) -> None:  # noqa: ANN001
        super().__init__(context)
        self.base_url = str(
            context.config.get("base_url") or "https://whitelabel.travel.yandex-net.ru/hotels"
        ).rstrip("/")
        self.token = context.credentials.get("token")

    def is_configured(self) -> tuple[bool, str | None]:
        if not self.token:
            return False, "Не задан OAuth-токен YANDEX_TRAVEL_TOKEN"
        return True, None

    def _http(self) -> ResilientHttpClient:
        return ResilientHttpClient(
            source_code=self.code,
            allowed_hosts=self.allowed_hosts,
            soft_timeout=self.context.soft_timeout,
            hard_timeout=self.context.hard_timeout,
            max_retries=self.context.max_retries,
            backoff_base=self.context.backoff_base,
            backoff_max=self.context.backoff_max,
            default_headers={"Authorization": f"OAuth {self.token}", "Accept": "application/json"},
        )

    def _resolve_geo_id(self, http: ResilientHttpClient, query: AccommodationQuery) -> int | None:
        explicit = query.city_source_ids.get("geo_id")
        if explicit is not None:
            try:
                return int(explicit)
            except (TypeError, ValueError):
                pass
        response = http.get(
            f"{self.base_url}/suggest",
            params={"query": query.city_name, "region_limit": 5, "hotel_limit": 0},
        )
        payload = response.json()
        regions = payload.get("regions") if isinstance(payload, dict) else None
        if not isinstance(regions, list):
            return None
        for region in regions:
            if isinstance(region, dict) and str(region.get("type", "")).upper() == "CITY":
                geo_id = region.get("geo_id")
                if geo_id is not None:
                    return int(geo_id)
        first = next((r for r in regions if isinstance(r, dict) and r.get("geo_id")), None)
        return int(first["geo_id"]) if first else None

    def collect_accommodation(self, query: AccommodationQuery) -> ConnectorResult:
        started = time.perf_counter()
        with self._http() as http:
            geo_id = self._resolve_geo_id(http, query)
            if geo_id is None:
                return ConnectorResult.failure(
                    source_code=self.code,
                    offer_type=OfferType.ACCOMMODATION,
                    outcome=ConnectorOutcome.EMPTY,
                    error_code="GEO_NOT_RESOLVED",
                    error_message=f"Не удалось определить geo_id для города {query.city_name}",
                    connector_version=self.version,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )

            params: dict[str, Any] = {
                "geo_id": geo_id,
                "checkin_date": query.check_in.isoformat(),
                "checkout_date": query.check_out.isoformat(),
                "adults": query.adults,
                "page_limit": int(self.context.config.get("page_limit", 50)),
                "order_by": "price-asc",
            }
            if query.children_ages:
                params["children_ages"] = ",".join(str(age) for age in query.children_ages)
            if query.stars.isdigit():
                params["stars"] = query.stars
            accomm_param = ACCOMM_TYPE_PARAM.get(query.accommodation_type)
            if accomm_param:
                params["accomm_type"] = accomm_param
            meal_param = MEAL_PARAM.get(query.meal_type)
            if meal_param:
                params["meal_type"] = meal_param
            if query.cancellation_filter == "FREE_CANCELLATION":
                params["free_cancellation"] = True

            raw_artifacts: list[RawArtifact] = []
            snippets: list[dict[str, Any]] = []
            max_polls = int(self.context.config.get("max_polls", 4))
            max_pages = int(self.context.config.get("max_pages", 3))

            page_token: str | None = None
            for _page in range(max_pages):
                page_params = dict(params)
                if page_token:
                    page_params["page_token"] = page_token

                payload: dict[str, Any] = {}
                for poll in range(max_polls):
                    response = http.get(f"{self.base_url}/search", params=page_params)
                    payload = response.json() if response.content else {}
                    raw_artifacts.append(
                        RawArtifact(
                            payload=payload,
                            endpoint=f"{self.base_url}/search",
                            request_params=page_params,
                            http_status=response.status_code,
                            latency_ms=response.latency_ms,
                        )
                    )
                    if not isinstance(payload, dict) or payload.get("complete", True):
                        break
                    time.sleep(min(0.8 * (poll + 1), 3.0))

                page_snippets = payload.get("hotel_snippets") if isinstance(payload, dict) else None
                if isinstance(page_snippets, list):
                    snippets.extend(item for item in page_snippets if isinstance(item, dict))
                page_token = payload.get("next_page_token") if isinstance(payload, dict) else None
                if not page_token:
                    break

            offers = self._parse(snippets, query)
            return ConnectorResult(
                source_code=self.code,
                offer_type=OfferType.ACCOMMODATION,
                outcome=ConnectorOutcome.SUCCESS if offers else ConnectorOutcome.EMPTY,
                offers=offers,
                raw_artifacts=raw_artifacts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                connector_version=self.version,
                diagnostics={"geo_id": geo_id, "snippet_count": len(snippets)},
            )

    def _parse(
        self, snippets: list[dict[str, Any]], query: AccommodationQuery
    ) -> list[ProviderAccommodationOffer]:
        offers: list[ProviderAccommodationOffer] = []
        nights = query.nights or 1
        for snippet in snippets:
            location = snippet.get("location") if isinstance(snippet.get("location"), dict) else {}
            settlement = location.get("settlement") if isinstance(location.get("settlement"), dict) else {}
            stars = snippet.get("stars")
            for top_offer in snippet.get("top_offers") or []:
                if not isinstance(top_offer, dict):
                    continue
                price_value = to_decimal((top_offer.get("price") or {}).get("value"))
                if price_value is None:
                    continue
                meal = (top_offer.get("meal_type") or {}).get("id")
                cancellation = (top_offer.get("cancellation") or {}).get("refund_type")
                offers.append(
                    ProviderAccommodationOffer(
                        source_offer_id=f"{snippet.get('hotel_id')}:{top_offer.get('name')}",
                        property_source_id=_as_str(snippet.get("hotel_id")),
                        property_name=_as_str(snippet.get("name")),
                        accommodation_type_raw=_as_str(snippet.get("accomm_type"))
                        or query.accommodation_type,
                        stars=int(stars) if isinstance(stars, int) and 1 <= stars <= 5 else None,
                        stars_unrated=stars == 0,
                        address=_as_str(location.get("address")),
                        city_name=_as_str(settlement.get("name")) or query.city_name,
                        latitude=_as_float(location.get("lat")),
                        longitude=_as_float(location.get("lon")),
                        currency=str((top_offer.get("price") or {}).get("currency") or "RUB"),
                        # Яндекс отдает цену за весь период размещения.
                        total_price=price_value,
                        price_basis="PER_ROOM_TOTAL",
                        price_per_night=(price_value / nights) if nights else None,
                        check_in=query.check_in,
                        check_out=query.check_out,
                        nights=nights,
                        room_name=_as_str(top_offer.get("name")),
                        capacity_confirmed_by_query=True,
                        meal_raw=MEAL_ID_MAP.get(str(meal), _as_str(meal)),
                        cancellation_raw=_as_str(cancellation),
                        review_score=_as_float(snippet.get("rating")),
                        review_count=_as_int(snippet.get("total_review_count")),
                        deeplink=_as_str(snippet.get("landing_url")),
                        source_payload={"is_corporate": top_offer.get("is_corporate")},
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
            with self._http() as http:
                response = http.get(
                    f"{self.base_url}/suggest",
                    params={"query": "Москва", "region_limit": 1, "hotel_limit": 0},
                )
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
            latency_ms=int((time.perf_counter() - started) * 1000),
            http_status=response.status_code,
            connector_version=self.version,
        )


def _as_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
