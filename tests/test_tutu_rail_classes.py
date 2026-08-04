"""Разбор карты мест Туту: цена за место по типу вагона.

Поиск Туту отдает одну строку на поезд с «ценой от» по всем классам сразу.
Пока класс вагона неизвестен, предложение не сопоставимо ни с РЖД, ни с
методикой (плацкарт и купе агрегируются раздельно), поэтому цена берется из
карты мест — единственного места, где Туту раскрывает класс.
"""

from __future__ import annotations

from decimal import Decimal

from tco.connectors.tutu_mcp import _cheapest_per_class, _seatmap_class_prices
from tco.core.enums import OfferType
from tco.normalization.classify import classify_rail_class


def seatmap(*cars: dict) -> dict:
    return {"seatmap_status": "ok", "cars": list(cars)}


def car(car_number: str, car_type: str, *prices: float) -> dict:
    return {
        "car_number": car_number,
        "car_type": car_type,
        "seat_groups": [
            {"group_index": i, "cheapest_fare": {"price": {"amount": p, "currency": "RUB"}}}
            for i, p in enumerate(prices)
        ],
    }


class TestSeatmapPrices:
    def test_minimum_per_car_type(self):
        payload = seatmap(
            car("1", "RESERVED_SEAT", 1735.21, 1800.0),
            car("5", "RESERVED_SEAT", 1690.5),
            car("10", "COMPARTMENT", 4791.5, 5200.0),
        )
        assert _seatmap_class_prices(payload) == {
            "RESERVED_SEAT": Decimal("1690.5"),
            "COMPARTMENT": Decimal("4791.5"),
        }

    def test_unwraps_mcp_envelope(self):
        """Ответ приходит списком с телом внутри — как у поиска."""
        payload = [{"body": seatmap(car("1", "COMPARTMENT", 4791.5))}]
        assert _seatmap_class_prices(payload) == {"COMPARTMENT": Decimal("4791.5")}

    def test_missing_layout_is_not_an_error(self):
        """Перевозчик может не отдавать схему — это не сбой сбора."""
        assert _seatmap_class_prices({"seatmap_status": "no_layout_for_carrier", "cars": []}) == {}

    def test_ignores_groups_without_price(self):
        payload = seatmap(
            {"car_number": "3", "car_type": "COMPARTMENT", "seat_groups": [{"group_index": 0}]},
            car("4", "COMPARTMENT", 5000.0),
        )
        assert _seatmap_class_prices(payload) == {"COMPARTMENT": Decimal("5000.0")}

    def test_garbage_payload_yields_nothing(self):
        for payload in (None, [], {}, "текст", {"cars": None}):
            assert _seatmap_class_prices(payload) == {}

    def test_car_types_feed_methodology_directly(self):
        """Значения Туту совпадают с RailClass, поэтому маппинг не нужен.

        Люкс и сидячие сознательно остаются нераспознанными: методика
        определяет только плацкарт и купе.
        """
        assert classify_rail_class("RESERVED_SEAT") is not None
        assert classify_rail_class("COMPARTMENT") is not None
        assert classify_rail_class("LUX") is None
        assert classify_rail_class("SEDENTARY") is None


class _Offer:
    """Минимальная замена ProviderRailOffer для проверки отбора."""

    def __init__(self, car_type: str | None, price: float) -> None:
        self.car_type_raw = car_type
        self.total_price = Decimal(str(price))

    def __repr__(self) -> str:  # pragma: no cover - для читаемых падений
        return f"{self.car_type_raw}@{self.total_price}"


class TestCheapestPerClass:
    def test_limit_applies_within_each_class(self):
        """Общий лимит вытеснил бы купе: плацкарт всегда дешевле."""
        offers = [
            _Offer("RESERVED_SEAT", 1700),
            _Offer("RESERVED_SEAT", 1750),
            _Offer("RESERVED_SEAT", 1800),
            _Offer("COMPARTMENT", 4800),
            _Offer("COMPARTMENT", 5200),
        ]

        kept = _cheapest_per_class(offers, OfferType.RAIL, limit=2)

        by_class: dict[str | None, list[Decimal]] = {}
        for item in kept:
            by_class.setdefault(item.car_type_raw, []).append(item.total_price)
        assert by_class["RESERVED_SEAT"] == [Decimal("1700"), Decimal("1750")]
        assert by_class["COMPARTMENT"] == [Decimal("4800"), Decimal("5200")]

    def test_flights_keep_plain_global_limit(self):
        offers = [_Offer(None, p) for p in (900, 700, 800)]
        kept = _cheapest_per_class(offers, OfferType.FLIGHT, limit=2)
        assert [o.total_price for o in kept] == [Decimal("700"), Decimal("800")]
