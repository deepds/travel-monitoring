"""Устойчивый HTTP-слой коннекторов.

Отвечает за таймауты, ретраи с экспоненциальным backoff и джиттером, разбор
типовых сбоев и allowlist хостов. Произвольные URL в коннекторах запрещены
(SCOPE-R E §7): базовый адрес берется из конфигурации источника, а каждый
запрос проверяется против ``allowed_hosts``.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

import httpx

from tco.core.errors import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorRateLimitError,
    ConnectorTimeoutError,
)
from tco.core.logging import get_logger

logger = get_logger(__name__)

#: Коды, которые имеет смысл повторять. 4xx не повторяем, кроме 408/429.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class HttpResponse:
    """Ответ вместе с техническими характеристиками вызова."""

    status_code: int
    content: bytes
    headers: dict[str, str]
    url: str
    latency_ms: int
    attempts: int

    def json(self) -> Any:
        import json

        return json.loads(self.content.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def host_allowed(url: str, allowed_hosts: Iterable[str]) -> bool:
    """Проверяет хост URL против allowlist источника."""
    allowed = {host.lower().lstrip(".") for host in allowed_hosts if host}
    if not allowed:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == item or host.endswith("." + item) for item in allowed)


class ResilientHttpClient:
    """Синхронный HTTP-клиент с ретраями (воркеры Celery синхронны)."""

    def __init__(
        self,
        *,
        source_code: str,
        allowed_hosts: Iterable[str],
        soft_timeout: float = 20.0,
        hard_timeout: float = 30.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        default_headers: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source_code = source_code
        self.allowed_hosts = list(allowed_hosts)
        self.soft_timeout = soft_timeout
        self.hard_timeout = hard_timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.default_headers = dict(default_headers or {})
        self._sleep = sleep
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------ #
    # Жизненный цикл
    # ------------------------------------------------------------------ #

    def __enter__(self) -> ResilientHttpClient:
        self._client = httpx.Client(
            timeout=httpx.Timeout(self.hard_timeout, connect=min(10.0, self.hard_timeout)),
            follow_redirects=True,
            headers={
                "User-Agent": "TravelCostObservatory/1.0 (+analytics; contact: ops@internal)",
                "Accept-Encoding": "gzip, deflate",
                **self.default_headers,
            },
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError("ResilientHttpClient используется вне контекстного менеджера")
        return self._client

    # ------------------------------------------------------------------ #
    # Запросы
    # ------------------------------------------------------------------ #

    def _backoff_delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.backoff_max)
        raw = self.backoff_base * (2**attempt)
        jitter = random.uniform(0.0, self.backoff_base)
        return min(raw + jitter, self.backoff_max)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
        expected_status: Iterable[int] = (200, 201, 202),
    ) -> HttpResponse:
        """Выполняет запрос с ретраями. Бросает ``ConnectorError`` при неудаче."""
        if not host_allowed(url, self.allowed_hosts):
            raise ConnectorError(
                f"Хост не входит в allowlist источника: {urlparse(url).hostname}",
                source_code=self.source_code,
                retryable=False,
                details={"url_host": urlparse(url).hostname, "allowed": self.allowed_hosts},
            )

        started = time.perf_counter()
        last_error: Exception | None = None
        attempts = 0

        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                response = self.client.request(
                    method,
                    url,
                    json=json_body,
                    params=dict(params) if params else None,
                    headers=dict(headers) if headers else None,
                    content=content,
                    timeout=httpx.Timeout(self.hard_timeout, read=self.soft_timeout),
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "Таймаут запроса к источнику",
                    source=self.source_code,
                    attempt=attempts,
                    url=url,
                )
                if attempt < self.max_retries:
                    self._sleep(self._backoff_delay(attempt))
                    continue
                raise ConnectorTimeoutError(
                    f"Таймаут после {attempts} попыток",
                    source_code=self.source_code,
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Транспортная ошибка источника",
                    source=self.source_code,
                    attempt=attempts,
                    error=str(exc),
                )
                if attempt < self.max_retries:
                    self._sleep(self._backoff_delay(attempt))
                    continue
                raise ConnectorError(
                    f"Транспортная ошибка: {exc}", source_code=self.source_code
                ) from exc

            latency_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code in expected_status:
                return HttpResponse(
                    status_code=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                    url=str(response.url),
                    latency_ms=latency_ms,
                    attempts=attempts,
                )

            if response.status_code in (401, 403):
                raise ConnectorAuthError(
                    f"Источник отклонил авторизацию (HTTP {response.status_code})",
                    source_code=self.source_code,
                    details={"http_status": response.status_code},
                )

            if response.status_code == 429:
                if attempt < self.max_retries:
                    self._sleep(self._backoff_delay(attempt, self._retry_after(response)))
                    continue
                raise ConnectorRateLimitError(
                    "Источник ограничил частоту запросов (HTTP 429)",
                    source_code=self.source_code,
                    details={"http_status": 429},
                )

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                self._sleep(self._backoff_delay(attempt, self._retry_after(response)))
                continue

            raise ConnectorError(
                f"Неожиданный ответ источника: HTTP {response.status_code}",
                source_code=self.source_code,
                retryable=response.status_code in RETRYABLE_STATUS,
                details={
                    "http_status": response.status_code,
                    "body_preview": response.text[:500],
                },
            )

        raise ConnectorError(
            f"Запрос не выполнен: {last_error}", source_code=self.source_code
        )

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("POST", url, **kwargs)
