"""Коннекторы источников данных.

Каждый коннектор изолирован от движка, сохраняет сырые ответы, возвращает
единый контракт, имеет таймауты и ретраи, и не может обрушить общий расчет.
"""

from tco.connectors.base import BaseConnector, ConnectorContext
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    ProviderAccommodationOffer,
    ProviderFlightOffer,
    ProviderRailOffer,
    TransportQuery,
)
from tco.connectors.registry import REGISTRY, build_context, create_connector

__all__ = [
    "AccommodationQuery",
    "BaseConnector",
    "ConnectorContext",
    "ConnectorResult",
    "ProviderAccommodationOffer",
    "ProviderFlightOffer",
    "ProviderRailOffer",
    "REGISTRY",
    "TransportQuery",
    "build_context",
    "create_connector",
]
