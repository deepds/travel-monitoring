"""Минимальный синхронный MCP-клиент (Streamable HTTP).

Реализует ту часть протокола, которая нужна платформе: ``initialize`` →
``notifications/initialized`` → ``tools/call`` / ``resources/read``.
Ответ сервера принимается как в виде ``application/json``, так и в виде
SSE-потока (``text/event-stream``) — оба варианта допускаются спецификацией
Streamable HTTP.

Отдельная реализация выбрана намеренно: официальный SDK асинхронный, а воркеры
Celery синхронны, и нам нужен полный контроль над таймаутами и ретраями.
"""

from __future__ import annotations

import json
from typing import Any

from tco.core.errors import ConnectorError, ConnectorSchemaError
from tco.core.logging import get_logger
from tco.connectors.http import HttpResponse, ResilientHttpClient

logger = get_logger(__name__)

PROTOCOL_VERSION = "2025-06-18"


def _parse_sse(text: str) -> list[Any]:
    """Извлекает JSON-объекты из SSE-потока."""
    messages: list[Any] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            messages.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return messages


def _extract_message(response: HttpResponse) -> dict[str, Any]:
    """Возвращает JSON-RPC сообщение из ответа любого поддерживаемого типа."""
    content_type = response.headers.get("content-type", "")
    body = response.text
    if "text/event-stream" in content_type:
        messages = _parse_sse(body)
        if not messages:
            raise ConnectorSchemaError("Пустой SSE-поток от MCP-сервера")
        # Последнее сообщение с ключом result/error — итоговое.
        for message in reversed(messages):
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return message
        return messages[-1]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ConnectorSchemaError(f"Некорректный JSON от MCP-сервера: {body[:200]}") from exc
    if isinstance(payload, list):
        for message in reversed(payload):
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return message
        raise ConnectorSchemaError("MCP-сервер вернул пакет без result/error")
    if not isinstance(payload, dict):
        raise ConnectorSchemaError("MCP-сервер вернул структуру неожиданного типа")
    return payload


class McpClient:
    """Клиент MCP поверх ``ResilientHttpClient``."""

    def __init__(self, http: ResilientHttpClient, endpoint: str, client_name: str = "tco") -> None:
        self.http = http
        self.endpoint = endpoint
        self.client_name = client_name
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self._request_id = 0
        self._initialized = False
        #: Все сырые обмены — для сохранения в raw storage.
        self.exchanges: list[dict[str, Any]] = []

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _call(self, method: str, params: dict[str, Any] | None, *, notification: bool = False) -> Any:
        request: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            request["params"] = params
        if not notification:
            request["id"] = self._next_id()

        response = self.http.post(
            self.endpoint,
            json_body=request,
            headers=self._headers(),
            expected_status=(200, 202),
        )

        returned_session = response.headers.get("mcp-session-id")
        if returned_session:
            self.session_id = returned_session

        if notification or response.status_code == 202 or not response.content:
            return None

        message = _extract_message(response)
        self.exchanges.append(
            {
                "method": method,
                "params": params,
                "latency_ms": response.latency_ms,
                "response": message,
            }
        )

        if "error" in message and message["error"]:
            error = message["error"]
            raise ConnectorError(
                f"MCP-сервер вернул ошибку {error.get('code')}: {error.get('message')}",
                source_code=self.http.source_code,
                retryable=False,
                details={"mcp_error": error},
            )
        return message.get("result")

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self.server_info
        result = self._call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "1.0.0"},
            },
        )
        self.server_info = result if isinstance(result, dict) else {}
        # Уведомление обязательно: без него часть серверов отклоняет tools/call.
        try:
            self._call("notifications/initialized", {}, notification=True)
        except ConnectorError as exc:  # pragma: no cover - сервер может не требовать
            logger.debug("notifications/initialized отклонено", error=str(exc))
        self._initialized = True
        return self.server_info

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._call("tools/list", {})
        if isinstance(result, dict):
            tools = result.get("tools", [])
            return tools if isinstance(tools, list) else []
        return []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Вызывает инструмент и возвращает распакованный результат.

        Порядок распаковки: ``structuredContent`` → JSON внутри текстового
        content → сырой текст.
        """
        self.initialize()
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise ConnectorSchemaError(f"Инструмент {name} вернул структуру неожиданного типа")

        if result.get("isError"):
            text = _content_to_text(result.get("content"))
            raise ConnectorError(
                f"Инструмент {name} завершился ошибкой: {text[:300]}",
                source_code=self.http.source_code,
                retryable=False,
            )

        structured = result.get("structuredContent")
        if isinstance(structured, dict) and structured:
            return structured

        text = _content_to_text(result.get("content"))
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def read_resource(self, uri: str) -> Any:
        self.initialize()
        result = self._call("resources/read", {"uri": uri})
        if not isinstance(result, dict):
            return None
        contents = result.get("contents") or []
        for item in contents:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return None


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts)
