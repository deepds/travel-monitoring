"""Выдача источника дочитывается до конца.

Поиск отдает не больше тридцати объектов за раз и не сортирует их: какие
именно тридцать попадут в ответ, решает источник. Пока читалась одна страница,
95 % запросов по отелям и 90 % по авиа упирались в потолок, и медиана считалась
по случайной трети рынка — по Казани 7 984 рубля вместо 6 923 по всем
84 объектам.

По ЖД пагинация не нужна и включаться не должна: на маршруте ходит три-пять
поездов, до потолка страницы далеко, и лишние обращения там ничего не дадут при
общем лимите темпа.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tco.connectors.base import ConnectorContext
from tco.connectors.contracts import AccommodationQuery
from tco.connectors.tutu_mcp import TutuMcpConnector

#: Схема инструмента поиска — то, что коннектор читает из ``tools/list``.
HOTELS_SCHEMA = {
    "properties": {
        "city_name": {"type": "string"},
        "check_in": {"type": "string"},
        "check_out": {"type": "string"},
        "adults": {"type": "integer"},
        "stars": {"type": "array", "items": {"type": "integer"}},
        "hotel_types": {"type": "array", "items": {"type": "string"}},
        "page": {"type": "integer", "minimum": 1, "maximum": 10},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 30},
    },
    "required": ["city_name"],
}

RAIL_SCHEMA = {
    "properties": {
        "from": {"type": "string"},
        "to": {"type": "string"},
        "date": {"type": "string"},
        "adults": {"type": "integer"},
        "page": {"type": "integer", "minimum": 1, "maximum": 10},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 30},
    },
    "required": ["from", "to", "date"],
}


def _hotel(index: int) -> dict:
    return {
        "hotel_id": f"h{index}",
        "name": f"Отель {index}",
        "stars": 3,
        "best_offer": {
            "price": {"amount": 6000 + index, "currency": "RUB"},
            "price_basis": "stay_total",
            "room_name": "Стандартный номер",
        },
    }


class _FakeMcp:
    """Источник, отдающий заданное число объектов на страницу."""

    def __init__(self, pages: list[int]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def list_tools(self):  # pragma: no cover — схемы подставляются напрямую
        return []

    def read_resource(self, _uri):  # pragma: no cover
        return None

    def call_tool(self, name: str, args: dict):
        self.calls.append({"tool": name, "args": dict(args)})
        page = int(args.get("page", 1))
        count = self.pages[page - 1] if page <= len(self.pages) else 0
        return {
            "stay": {"nights": 1, "check_in": "2026-08-20", "check_out": "2026-08-21"},
            "hotels": [_hotel(page * 100 + item) for item in range(count)],
        }


class _NoHttp:
    """Заглушка транспорта: сеть в этих тестах не нужна."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _connector(mcp: _FakeMcp, monkeypatch, *, max_pages: int = 10) -> TutuMcpConnector:
    """Коннектор с подмененным транспортом и заранее известной схемой.

    Подменяются ровно две вещи — HTTP-клиент и создание MCP-клиента, — а весь
    разбор ответа и построение аргументов остаются настоящими: иначе тест
    проверял бы заглушку, а не коннектор.
    """
    from tco.connectors import tutu_mcp as module

    context = ConnectorContext(
        source_code="tutu_mcp",
        source_name="Туту",
        allowed_hosts=["mcp.tutu.ru"],
        config={"per_page": 50},
        max_pages=max_pages,
    )
    connector = TutuMcpConnector(context)
    connector._tool_schemas = {  # noqa: SLF001 — схема вместо сетевого tools/list
        TutuMcpConnector.TOOL_HOTELS: HOTELS_SCHEMA,
        TutuMcpConnector.TOOL_RAIL: RAIL_SCHEMA,
    }
    connector._geo_index = {}  # noqa: SLF001
    monkeypatch.setattr(connector, "_http", lambda: _NoHttp())
    monkeypatch.setattr(module, "McpClient", lambda *args, **kwargs: mcp)
    return connector


@pytest.fixture()
def accommodation_query() -> AccommodationQuery:
    check_in = date.today() + timedelta(days=14)
    return AccommodationQuery(
        city_code="KZN",
        city_name="Казань",
        check_in=check_in,
        check_out=check_in + timedelta(days=1),
        adults=1,
        children_ages=(),
        accommodation_type="HOTEL",
        stars="3",
        meal_type="ANY",
        cancellation_filter="ANY",
    )


class TestHotelPagination:
    def test_reads_every_page_until_a_short_one(self, accommodation_query, monkeypatch):
        """Казань: 30 + 30 + 24. Неполная страница — конец выдачи."""
        mcp = _FakeMcp([30, 30, 24])
        connector = _connector(mcp, monkeypatch)

        result = connector.collect_accommodation(accommodation_query)

        assert len(mcp.calls) == 3, "обход прекратился раньше конца выдачи"
        assert len(result.offers) == 84
        assert result.is_partial is False, "выдача дочитана целиком"

    def test_page_number_is_sent_from_the_second_page(self, accommodation_query, monkeypatch):
        mcp = _FakeMcp([30, 10])
        connector = _connector(mcp, monkeypatch)

        connector.collect_accommodation(accommodation_query)

        assert "page" not in mcp.calls[0]["args"], "первая страница не нумеруется"
        assert mcp.calls[1]["args"]["page"] == 2

    def test_hitting_the_page_limit_marks_the_result_partial(
        self, accommodation_query, monkeypatch
    ):
        """Обрезанная выдача обязана быть отличима от полной."""
        mcp = _FakeMcp([30] * 12)
        connector = _connector(mcp, monkeypatch, max_pages=3)

        result = connector.collect_accommodation(accommodation_query)

        assert len(mcp.calls) == 3
        assert result.is_partial is True

    def test_apartments_are_filtered_by_the_source(self, accommodation_query, monkeypatch):
        """Отсев на стороне источника: каждый апартамент вытесняет отель."""
        mcp = _FakeMcp([5])
        connector = _connector(mcp, monkeypatch)

        connector.collect_accommodation(accommodation_query)

        assert mcp.calls[0]["args"]["hotel_types"] == ["hotel"]

    def test_empty_first_page_stops_immediately(self, accommodation_query, monkeypatch):
        mcp = _FakeMcp([0])
        connector = _connector(mcp, monkeypatch)

        result = connector.collect_accommodation(accommodation_query)

        assert len(mcp.calls) == 1
        assert result.offers == []


class TestRailIsNotPaginated:
    """На маршруте три-пять поездов: до потолка страницы там не доходит."""

    def test_rail_search_asks_for_one_page(self, monkeypatch):
        mcp = _FakeMcp([30, 30])
        connector = _connector(mcp, monkeypatch)
        props = connector._properties(TutuMcpConnector.TOOL_RAIL)  # noqa: SLF001

        pages, stats = connector._fetch_pages(  # noqa: SLF001
            mcp, TutuMcpConnector.TOOL_RAIL, {"page_size": 30}, props, paginate=False
        )

        assert len(pages) == 1
        assert stats["pages_limit"] == 1
