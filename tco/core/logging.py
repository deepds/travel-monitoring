"""Структурированное логирование.

Формат по умолчанию — JSON-строки, пригодные для сбора в любой стек.
В логи никогда не попадают заголовки авторизации и токены: за это отвечает
``redact()``, применяемый ко всем словарям контекста.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

from tco.core.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_SENSITIVE_KEYS = {
    "authorization",
    "auth",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "x-api-key",
    "password",
    "secret",
    "client_secret",
    "cookie",
    "set-cookie",
    "jwt",
    "oauth",
}

_REDACTED = "***"


def redact(payload: Any, _depth: int = 0) -> Any:
    """Рекурсивно вычищает секреты из произвольной структуры."""
    if _depth > 8:
        return payload
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                cleaned[key] = _REDACTED
            else:
                cleaned[key] = redact(value, _depth + 1)
        return cleaned
    if isinstance(payload, (list, tuple)):
        return [redact(item, _depth + 1) for item in payload]
    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        correlation_id = correlation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name} :: {record.getMessage()}"
        extra = getattr(record, "context", None)
        if isinstance(extra, dict) and extra:
            base += " " + json.dumps(redact(extra), ensure_ascii=False, default=str)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def configure_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return
    settings = get_settings()
    # На Windows консоль по умолчанию не в UTF-8 — кириллица в логах иначе
    # приводит к UnicodeEncodeError внутри самого логгера.
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - зависит от окружения
            pass
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "s3transfer", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


class BoundLogger:
    """Тонкая обертка над ``logging.Logger`` с контекстом-словарем."""

    __slots__ = ("_logger", "_context")

    def __init__(self, logger: logging.Logger, context: dict[str, Any] | None = None) -> None:
        self._logger = logger
        self._context = context or {}

    def bind(self, **kwargs: Any) -> BoundLogger:
        return BoundLogger(self._logger, {**self._context, **kwargs})

    def _log(self, level: int, message: str, exc_info: bool = False, **kwargs: Any) -> None:
        context = {**self._context, **kwargs}
        self._logger.log(level, message, extra={"context": context}, exc_info=exc_info)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, exc_info=exc_info, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, exc_info=True, **kwargs)


def get_logger(name: str, **context: Any) -> BoundLogger:
    configure_logging()
    return BoundLogger(logging.getLogger(name), context)
