"""Сопоставление источников по одним и тем же поездам (Туту против РЖД).

Правило группировки проверяется на заданном наборе предложений, а не через
песочницу: сид синтетических коннекторов включает код источника, поэтому
``sandbox_alpha`` и ``sandbox_beta`` генерируют разные номера поездов и
межисточниковых групп не образуют.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tco.core.enums import OfferType, RailClass, ValidityStatus
from tco.db.models.offer import Offer, RailOffer
from tco.services.offers import build_rail_groups

DEPARTURE = datetime(2026, 9, 18, 21, 50, tzinfo=UTC)


def make_offer(
    source_code: str,
    price: str,
    *,
    group_id: uuid.UUID | None,
    car_type: str = RailClass.COMPARTMENT.value,
    train: str = "029А",
    departure: datetime = DEPARTURE,
) -> Offer:
    """Предложение в памяти — без сессии и без записи в БД."""
    item = Offer(
        id=uuid.uuid4(),
        source_code=source_code,
        offer_type=OfferType.RAIL.value,
        currency="RUB",
        total_price=Decimal(price),
        validity_status=ValidityStatus.VALID.value,
        equivalence_group_id=group_id,
        is_duplicate=False,
        is_outlier=False,
        exclusion_reason="NONE",
        deeplink=None,
    )
    item.rail = RailOffer(
        outbound_train_number=train,
        inbound_train_number="030А",
        outbound_departure_at=departure,
        outbound_arrival_at=departure + timedelta(hours=8),
        outbound_duration_minutes=480,
        car_type=car_type,
        car_type_raw="Купе" if car_type == RailClass.COMPARTMENT.value else "Плацкарт",
        carriers=["ФПК"],
        origin_station_name="Москва-Пасс.",
        destination_station_name="Калининград-Пасс.",
        price_per_place_outbound=Decimal(price) / 2,
        price_per_place_inbound=Decimal(price) / 2,
        passenger_count=1,
        is_round_trip=True,
        refundability="REFUNDABLE",
    )
    return item


class TestGrouping:
    def test_same_train_from_two_sources_forms_one_group(self):
        group = uuid.uuid4()
        rows = [
            make_offer("tutu_mcp", "12400", group_id=group),
            make_offer("rzd", "12980", group_id=group),
        ]

        result = build_rail_groups(rows)

        assert result["summary"]["group_count"] == 1
        assert result["summary"]["cross_source_group_count"] == 1
        assert result["sources"] == ["rzd", "tutu_mcp"]

        row = result["groups"][0]
        assert set(row["prices"]) == {"tutu_mcp", "rzd"}
        assert row["prices"]["tutu_mcp"]["total_price"] == 12400.0
        assert row["prices"]["rzd"]["total_price"] == 12980.0
        assert row["cheapest_source"] == "tutu_mcp"
        assert row["delta_absolute"] == 580.0
        assert row["delta_ratio"] == pytest.approx(580 / 12400)
        assert row["outbound_train_number"] == "029А"

    def test_different_car_types_are_not_merged(self):
        """Нераспознанный тип вагона не должен схлопнуть купе с плацкартом."""
        group = uuid.uuid4()
        rows = [
            make_offer("tutu_mcp", "12400", group_id=group),
            make_offer(
                "rzd", "6100", group_id=group, car_type=RailClass.RESERVED_SEAT.value
            ),
        ]

        result = build_rail_groups(rows)

        assert result["summary"]["group_count"] == 2
        assert result["summary"]["cross_source_group_count"] == 0
        # Расхождение помечается, чтобы это не выглядело как две разные поездки.
        assert all(row["car_type_ambiguous"] for row in result["groups"])

    def test_offer_without_equivalence_key_is_kept_separate(self):
        """Предложение без ключа не должно молча выпасть из сравнения."""
        rows = [
            make_offer("tutu_mcp", "12400", group_id=uuid.uuid4()),
            make_offer("rzd", "12980", group_id=None),
        ]

        result = build_rail_groups(rows)

        assert result["summary"]["group_count"] == 2
        assert result["summary"]["single_source_group_count"] == 2
        ungrouped = [row for row in result["groups"] if row["equivalence_group_id"] is None]
        assert len(ungrouped) == 1
        assert ungrouped[0]["car_type_ambiguous"] is False

    def test_cheapest_fare_per_source_wins(self):
        """Несколько тарифов одного источника не должны затирать друг друга."""
        group = uuid.uuid4()
        rows = [
            make_offer("tutu_mcp", "15000", group_id=group),
            make_offer("tutu_mcp", "12400", group_id=group),
            make_offer("rzd", "12980", group_id=group),
        ]

        result = build_rail_groups(rows)

        row = result["groups"][0]
        assert row["source_count"] == 2
        assert row["prices"]["tutu_mcp"]["total_price"] == 12400.0
        assert row["cheapest_source"] == "tutu_mcp"

    def test_schedule_disagreement_is_surfaced(self):
        """Разное время отправления у источников — сигнал качества данных."""
        group = uuid.uuid4()
        rows = [
            make_offer("tutu_mcp", "12400", group_id=group),
            make_offer(
                "rzd", "12980", group_id=group, departure=DEPARTURE + timedelta(minutes=15)
            ),
        ]

        result = build_rail_groups(rows)

        assert result["groups"][0]["sources_disagree_on_schedule"] is True

    def test_cross_source_only_hides_single_source_groups(self):
        shared = uuid.uuid4()
        rows = [
            make_offer("tutu_mcp", "12400", group_id=shared),
            make_offer("rzd", "12980", group_id=shared),
            make_offer("tutu_mcp", "9900", group_id=uuid.uuid4(), train="101Б"),
        ]

        assert build_rail_groups(rows)["summary"]["group_count"] == 2

        filtered = build_rail_groups(rows, cross_source_only=True)
        assert filtered["summary"]["group_count"] == 1
        assert filtered["groups"][0]["source_count"] == 2


class TestSummary:
    def test_counts_are_consistent(self):
        shared = uuid.uuid4()
        rows = [
            make_offer("tutu_mcp", "12400", group_id=shared),
            make_offer("rzd", "12980", group_id=shared),
            make_offer("rzd", "8400", group_id=uuid.uuid4(), train="055Г"),
        ]

        summary = build_rail_groups(rows)["summary"]

        assert summary["group_count"] == 2
        assert summary["cross_source_group_count"] == 1
        assert summary["single_source_group_count"] == 1
        assert summary["median_delta_ratio"] == summary["max_delta_ratio"]

    def test_empty_input_yields_zeroed_summary(self):
        result = build_rail_groups([])

        assert result["groups"] == []
        assert result["sources"] == []
        assert result["summary"]["group_count"] == 0
        assert result["summary"]["median_delta_ratio"] is None
