"""Доменные ошибки и единый error envelope (DELTA §5.3)."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Базовая ошибка приложения, отображаемая в error envelope."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    message: str = "Внутренняя ошибка"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        self.details = details or {}
        super().__init__(self.message)

    def envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "request_id": request_id,
            }
        }


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404
    message = "Объект не найден"


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    http_status = 422
    message = "Некорректные входные данные"


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = 409
    message = "Конфликт состояния"


class AuthenticationError(AppError):
    code = "UNAUTHENTICATED"
    http_status = 401
    message = "Требуется авторизация"


class AuthorizationError(AppError):
    code = "FORBIDDEN"
    http_status = 403
    message = "Недостаточно прав"


class RateLimitError(AppError):
    code = "RATE_LIMITED"
    http_status = 429
    message = "Превышен лимит запросов"


class ScenarioValidationError(ValidationError):
    """Сценарий не может быть рассчитан — внешние запросы не выполняются."""

    code = "SCENARIO_UNSUPPORTED"
    http_status = 422
    message = "Сценарий не поддерживается"


class UnsupportedRouteError(ScenarioValidationError):
    code = "UNSUPPORTED_ROUTE"
    message = "Маршрут не поддерживается"


class ProfileImmutableError(ConflictError):
    code = "PROFILE_IMMUTABLE"
    message = "Активный профиль расчета неизменяем"


class SnapshotImmutableError(ConflictError):
    code = "SNAPSHOT_IMMUTABLE"
    message = "Завершенный Market Snapshot неизменяем"


class ConnectorError(AppError):
    """Ошибка обращения к внешнему источнику. Никогда не обрушивает расчет."""

    code = "CONNECTOR_ERROR"
    http_status = 502
    message = "Ошибка источника данных"

    def __init__(
        self,
        message: str,
        *,
        source_code: str | None = None,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details={**(details or {}), "source_code": source_code})
        self.source_code = source_code
        self.retryable = retryable


class ConnectorTimeoutError(ConnectorError):
    code = "CONNECTOR_TIMEOUT"
    message = "Источник не ответил за отведенное время"


class ConnectorRateLimitError(ConnectorError):
    code = "CONNECTOR_RATE_LIMITED"
    message = "Источник ограничил частоту запросов"


class ConnectorAuthError(ConnectorError):
    code = "CONNECTOR_AUTH_ERROR"
    message = "Источник отклонил авторизацию"

    def __init__(self, message: str, *, source_code: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, source_code=source_code, retryable=False, **kwargs)


class ConnectorSchemaError(ConnectorError):
    code = "CONNECTOR_SCHEMA_ERROR"
    message = "Ответ источника не соответствует ожидаемой схеме"

    def __init__(self, message: str, *, source_code: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, source_code=source_code, retryable=False, **kwargs)


class StorageError(AppError):
    code = "STORAGE_ERROR"
    http_status = 503
    message = "Хранилище недоступно"


class CacheUnavailableError(AppError):
    """Кэш недоступен — расчет обязан продолжиться без него."""

    code = "CACHE_UNAVAILABLE"
    http_status = 503
    message = "Кэш недоступен"


class BrokerUnavailableError(AppError):
    """Брокер задач недоступен — фоновую операцию невозможно поставить в очередь.

    Отдается вместо 500: состояние восстановимо, запрос имеет смысл повторить.
    """

    code = "BROKER_UNAVAILABLE"
    http_status = 503
    message = "Очередь задач недоступна, повторите запрос позже"


def error_envelope(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }
