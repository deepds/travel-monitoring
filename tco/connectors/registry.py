"""Реестр коннекторов и сборка их контекста.

Реестр — единственный способ получить экземпляр коннектора. Он же выполняет
allowlist-проверку: коннектор не может быть создан для источника, чьи хосты
не заданы ни в коде, ни в конфигурации.
"""

from __future__ import annotations

from typing import Iterable

from tco.core.config import Settings, get_settings
from tco.core.errors import NotFoundError
from tco.connectors.base import BaseConnector, ConnectorContext
from tco.connectors.rzd import RzdConnector
from tco.connectors.sandbox import SandboxAlphaConnector, SandboxBetaConnector
from tco.connectors.travelline import TravellineConnector
from tco.connectors.tutu_mcp import TutuMcpConnector
from tco.connectors.yandex_travel import YandexTravelConnector

CONNECTOR_CLASSES: tuple[type[BaseConnector], ...] = (
    TutuMcpConnector,
    RzdConnector,
    YandexTravelConnector,
    TravellineConnector,
    SandboxAlphaConnector,
    SandboxBetaConnector,
)

REGISTRY: dict[str, type[BaseConnector]] = {cls.code: cls for cls in CONNECTOR_CLASSES}


def available_codes() -> list[str]:
    return sorted(REGISTRY)


def connector_class(code: str) -> type[BaseConnector]:
    try:
        return REGISTRY[code]
    except KeyError as exc:
        raise NotFoundError(
            f"Коннектор {code} не зарегистрирован",
            details={"available": available_codes()},
        ) from exc


def credentials_for(code: str, settings: Settings | None = None) -> dict[str, str | None]:
    """Учетные данные источника — только из окружения, никогда из БД."""
    settings = settings or get_settings()
    if code == YandexTravelConnector.code:
        return {"token": settings.yandex_travel_token}
    if code == TravellineConnector.code:
        return {
            "client_id": settings.travelline_client_id,
            "client_secret": settings.travelline_client_secret,
        }
    return {}


def default_config_for(code: str, settings: Settings | None = None) -> dict[str, object]:
    """Конфигурация источника по умолчанию (адреса — только из настроек)."""
    settings = settings or get_settings()
    if code == TutuMcpConnector.code:
        return {"endpoint": settings.tutu_mcp_url, "per_page": 50}
    if code == RzdConnector.code:
        return {"base_url": settings.rzd_base_url, "roundtrip_legs_per_direction": 8}
    if code == YandexTravelConnector.code:
        return {"base_url": settings.yandex_travel_base_url, "page_limit": 50}
    if code == TravellineConnector.code:
        return {
            "base_url": settings.travelline_base_url,
            "auth_url": settings.travelline_auth_url,
        }
    return {}


def build_context(
    *,
    code: str,
    name: str = "",
    allowed_hosts: Iterable[str] | None = None,
    config: dict | None = None,
    request_id: str = "",
    settings: Settings | None = None,
    max_offers: int | None = None,
    soft_timeout: float | None = None,
    hard_timeout: float | None = None,
    max_retries: int | None = None,
    html_storage_allowed: bool = False,
    observation_seed: str = "",
    deadline: float | None = None,
    max_pages: int | None = None,
) -> ConnectorContext:
    settings = settings or get_settings()
    cls = connector_class(code)
    merged_config = {**default_config_for(code, settings), **(config or {})}
    if observation_seed:
        merged_config["observation_seed"] = observation_seed
    hosts = list(allowed_hosts or ()) or list(cls.default_allowed_hosts)
    return ConnectorContext(
        source_code=code,
        source_name=name or cls.title,
        allowed_hosts=hosts,
        config=merged_config,
        credentials=credentials_for(code, settings),
        soft_timeout=soft_timeout if soft_timeout is not None else settings.connector_soft_timeout_seconds,
        hard_timeout=hard_timeout if hard_timeout is not None else settings.connector_hard_timeout_seconds,
        max_retries=max_retries if max_retries is not None else settings.connector_max_retries,
        backoff_base=settings.connector_backoff_base_seconds,
        backoff_max=settings.connector_backoff_max_seconds,
        max_offers=max_offers if max_offers is not None else 300,
        is_synthetic=cls.is_synthetic,
        html_storage_allowed=html_storage_allowed,
        request_id=request_id,
        deadline=deadline,
        max_pages=max_pages if max_pages is not None else settings.collection_max_pages,
    )


def create_connector(code: str, context: ConnectorContext | None = None, **kwargs) -> BaseConnector:
    cls = connector_class(code)
    return cls(context or build_context(code=code, **kwargs))


def descriptors() -> list[dict]:
    """Паспорта всех коннекторов — используется при bootstrap источников."""
    return [cls(build_context(code=cls.code)).descriptor() for cls in CONNECTOR_CLASSES]
