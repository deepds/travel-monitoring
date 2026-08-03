"""Middleware: идентификаторы запроса, журналирование, ограничение частоты."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tco.core.config import get_settings
from tco.core.errors import RateLimitError
from tco.core.logging import correlation_id_var, get_logger, request_id_var

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"
CORRELATION_ID_HEADER = "X-Correlation-Id"

#: Пути, которые не засоряют журнал и не тратят бюджет rate limit.
_QUIET_PATHS = frozenset({"/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Проставляет request_id и correlation_id и журналирует итог запроса."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        rid = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
        correlation = request.headers.get(CORRELATION_ID_HEADER) or rid

        request.state.request_id = rid
        request.state.correlation_id = correlation
        token_rid = request_id_var.set(rid)
        token_corr = correlation_id_var.set(correlation)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "Необработанная ошибка запроса",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        finally:
            request_id_var.reset(token_rid)
            correlation_id_var.reset(token_corr)

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = rid
        response.headers[CORRELATION_ID_HEADER] = correlation

        if request.url.path not in _QUIET_PATHS:
            logger.info(
                "Запрос обработан",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Простое окно частоты запросов на пользователя/IP.

    Достаточно для MVP с одним процессом API. При горизонтальном
    масштабировании счетчик нужно вынести в Redis — отражено в LIMITATIONS.
    """

    def __init__(self, app, limit_per_minute: int | None = None) -> None:  # noqa: ANN001
        super().__init__(app)
        settings = get_settings()
        self.limit = limit_per_minute or settings.rate_limit_per_minute
        self.enabled = settings.rate_limit_enabled
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _identity(self, request: Request) -> str:
        auth = request.headers.get("authorization")
        if auth:
            # Токен в ключ не попадает — только его короткий отпечаток.
            return f"token:{hash(auth) & 0xFFFFFFFF:08x}"
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or request.url.path in _QUIET_PATHS:
            return await call_next(request)

        key = self._identity(request)
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60.0:
            window.popleft()

        if len(window) >= self.limit:
            error = RateLimitError(
                f"Превышен лимит {self.limit} запросов в минуту",
                details={"limit_per_minute": self.limit, "retry_after_seconds": 60},
            )
            logger.warning("Запрос отклонен ограничителем частоты", identity=key, limit=self.limit)
            return JSONResponse(
                status_code=error.http_status,
                content=error.envelope(getattr(request.state, "request_id", None)),
                headers={"Retry-After": "60"},
            )

        window.append(now)
        # Периодически подчищаем словарь, чтобы он не рос бесконечно.
        if len(self._hits) > 4096:
            stale = [k for k, v in self._hits.items() if not v or now - v[-1] > 300.0]
            for k in stale:
                self._hits.pop(k, None)

        return await call_next(request)
