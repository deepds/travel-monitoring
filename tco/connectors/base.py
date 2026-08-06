"""Базовый класс коннектора и защитная обвязка.

Коннектор изолирован от движка: он ничего не знает про профили, агрегацию и
Quality Score. Его единственная обязанность — вернуть ``ConnectorResult``
и сырые артефакты. Любая ошибка превращается в результат с соответствующим
``ConnectorOutcome`` и не выходит наружу.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from tco.core.enums import ConnectorOutcome, OfferType, SourceCategory
from tco.core.errors import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorRateLimitError,
    ConnectorSchemaError,
    ConnectorTimeoutError,
)
from tco.core.logging import get_logger
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    TransportQuery,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class ConnectorContext:
    """Всё, что нужно коннектору помимо запроса."""

    source_code: str
    source_name: str
    allowed_hosts: list[str]
    config: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, str | None] = field(default_factory=dict)
    soft_timeout: float = 20.0
    hard_timeout: float = 30.0
    max_retries: int = 2
    backoff_base: float = 0.5
    backoff_max: float = 8.0
    max_offers: int = 300
    #: Самоограничение темпа обращений к источнику. ``None`` — без ограничения.
    #: Публичные источники не публикуют лимитов, поэтому значение задается
    #: нами в каталоге источников и хранится в ``Source.rate_limit_per_minute``.
    rate_limit_per_minute: int | None = None
    is_synthetic: bool = False
    html_storage_allowed: bool = False
    request_id: str = ""
    #: Момент по ``time.monotonic()``, после которого коннектор прекращает
    #: добирать данные и возвращает собранное. ``None`` — без ограничения.
    #:
    #: Нужен потому, что предел времени задачи Celery снимает ее целиком —
    #: вместе с уже полученными предложениями. Внутренний бюджет позволяет
    #: остановить перебор (страницы выдачи, карты мест) добровольно и сохранить
    #: то, что успели собрать, пометив результат частичным.
    deadline: float | None = None
    #: Сколько страниц выдачи дочитывать там, где источник ее разбивает.
    #: Единица — прежнее поведение: только первая страница.
    max_pages: int = 1

    @property
    def budget_exhausted(self) -> bool:
        """Бюджет времени исчерпан — перебор пора прекращать."""
        return self.deadline is not None and time.monotonic() >= self.deadline

    def seconds_left(self) -> float | None:
        """Сколько времени осталось; ``None`` — бюджет не задан."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


class BaseConnector(ABC):
    """Контракт коннектора."""

    #: Уникальный код источника (совпадает с ``Source.code``).
    code: str = ""
    #: Человекочитаемое имя.
    title: str = ""
    category: SourceCategory = SourceCategory.TRANSPORT
    #: Типы предложений, которые умеет отдавать коннектор.
    supported_offer_types: tuple[OfferType, ...] = ()
    version: str = "1.0.0"
    #: Требуются ли учетные данные для работы.
    requires_credentials: bool = False
    #: Хосты, к которым коннектору разрешено обращаться.
    default_allowed_hosts: tuple[str, ...] = ()
    is_synthetic: bool = False

    def __init__(self, context: ConnectorContext) -> None:
        self.context = context
        self.log = logger.bind(source=self.code, request_id=context.request_id)

    # ------------------------------------------------------------------ #
    # Реализуют наследники
    # ------------------------------------------------------------------ #

    def collect_transport(self, query: TransportQuery) -> ConnectorResult:
        """Собирает транспортные предложения."""
        raise NotImplementedError

    def collect_accommodation(self, query: AccommodationQuery) -> ConnectorResult:
        """Собирает предложения по проживанию."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> ConnectorResult:
        """Легковесная проверка доступности источника."""

    def is_configured(self) -> tuple[bool, str | None]:
        """Проверяет наличие учетных данных. ``(готов, причина_если_нет)``."""
        if not self.requires_credentials:
            return True, None
        missing = [key for key, value in self.context.credentials.items() if not value]
        if missing:
            return False, f"Не заданы учетные данные: {', '.join(sorted(missing))}"
        return True, None

    # ------------------------------------------------------------------ #
    # Защитная обвязка
    # ------------------------------------------------------------------ #

    def safe_collect(
        self,
        offer_type: OfferType,
        query: TransportQuery | AccommodationQuery,
    ) -> ConnectorResult:
        """Вызывает коннектор так, что наружу не выходит ни одно исключение.

        Падение одного источника не должно обрушивать остальные
        (SCOPE-R E §4, §3).
        """
        started = time.perf_counter()

        if offer_type not in self.supported_offer_types:
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=offer_type,
                outcome=ConnectorOutcome.UNSUPPORTED,
                error_code="UNSUPPORTED_OFFER_TYPE",
                error_message=f"Источник не поддерживает тип предложений {offer_type}",
                connector_version=self.version,
            )

        configured, reason = self.is_configured()
        if not configured:
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=offer_type,
                outcome=ConnectorOutcome.DISABLED,
                error_code="NOT_CONFIGURED",
                error_message=reason or "Источник не сконфигурирован",
                connector_version=self.version,
            )

        try:
            if offer_type == OfferType.ACCOMMODATION:
                assert isinstance(query, AccommodationQuery)
                result = self.collect_accommodation(query)
            else:
                assert isinstance(query, TransportQuery)
                result = self.collect_transport(query)
        except ConnectorTimeoutError as exc:
            return self._as_failure(offer_type, ConnectorOutcome.TIMEOUT, exc, started)
        except ConnectorRateLimitError as exc:
            return self._as_failure(offer_type, ConnectorOutcome.RATE_LIMITED, exc, started)
        except ConnectorAuthError as exc:
            return self._as_failure(offer_type, ConnectorOutcome.AUTH_ERROR, exc, started)
        except ConnectorSchemaError as exc:
            return self._as_failure(offer_type, ConnectorOutcome.SCHEMA_ERROR, exc, started)
        except ConnectorError as exc:
            return self._as_failure(offer_type, ConnectorOutcome.TRANSPORT_ERROR, exc, started)
        except NotImplementedError:
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=offer_type,
                outcome=ConnectorOutcome.UNSUPPORTED,
                error_code="NOT_IMPLEMENTED",
                error_message="Метод сбора не реализован для этого типа предложений",
                connector_version=self.version,
            )
        except Exception as exc:  # noqa: BLE001 — намеренный барьер вокруг источника
            self.log.exception("Непредвиденная ошибка коннектора", error=str(exc))
            return ConnectorResult.failure(
                source_code=self.code,
                offer_type=offer_type,
                outcome=ConnectorOutcome.TRANSPORT_ERROR,
                error_code=type(exc).__name__,
                error_message=str(exc),
                connector_version=self.version,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        if result.latency_ms is None:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
        if result.offers and len(result.offers) > self.context.max_offers:
            result.diagnostics["truncated_from"] = len(result.offers)
            result.offers = result.offers[: self.context.max_offers]
        if result.outcome == ConnectorOutcome.SUCCESS and not result.offers:
            result.outcome = ConnectorOutcome.EMPTY
        return result

    def _as_failure(
        self,
        offer_type: OfferType,
        outcome: ConnectorOutcome,
        exc: ConnectorError,
        started: float,
    ) -> ConnectorResult:
        self.log.warning(
            "Источник вернул ошибку",
            outcome=str(outcome),
            error_code=exc.code,
            error=exc.message,
        )
        return ConnectorResult.failure(
            source_code=self.code,
            offer_type=offer_type,
            outcome=outcome,
            error_code=exc.code,
            error_message=exc.message,
            connector_version=self.version,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------------ #
    # Вспомогательное
    # ------------------------------------------------------------------ #

    @property
    def allowed_hosts(self) -> list[str]:
        return self.context.allowed_hosts or list(self.default_allowed_hosts)

    def descriptor(self) -> dict[str, Any]:
        """Паспорт коннектора для админского экрана и bootstrap."""
        return {
            "code": self.code,
            "title": self.title,
            "category": str(self.category),
            "offer_types": [str(item) for item in self.supported_offer_types],
            "version": self.version,
            "requires_credentials": self.requires_credentials,
            "allowed_hosts": list(self.default_allowed_hosts),
            "is_synthetic": self.is_synthetic,
        }
