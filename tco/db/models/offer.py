"""Нормализованные предложения (Offer Snapshot).

Общая часть — в ``offers``; специализированные поля — в отдельных таблицах
с четкой схемой (SCOPE-R P §2), связанных 1:1 по ``offer_id``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tco.core.enums import (
    ClassificationStatus,
    ExclusionReason,
    OfferType,
    ValidityStatus,
)
from tco.db.base import Base, JSONB, Money, TZDateTime, UUIDPrimaryKeyMixin


class Offer(UUIDPrimaryKeyMixin, Base):
    """Нормализованное рыночное предложение внутри Market Snapshot."""

    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_snapshot_type", "market_snapshot_id", "offer_type"),
        Index("ix_offers_snapshot_source", "market_snapshot_id", "source_id"),
        Index("ix_offers_fingerprint", "market_snapshot_id", "technical_fingerprint"),
        Index("ix_offers_equivalence", "equivalence_group_id"),
        Index("ix_offers_collected", "collected_at"),
    )

    market_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("travel_scenarios.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_offer_id: Mapped[str | None] = mapped_column(String(255))
    offer_type: Mapped[str] = mapped_column(String(24), nullable=False)

    collected_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    #: Итоговая цена предложения в единицах, определенных методикой:
    #: транспорт — все пассажиры туда и обратно; проживание — номер за весь период.
    total_price: Mapped[Decimal | None] = mapped_column(Money)
    price_per_night: Mapped[Decimal | None] = mapped_column(Money)

    validity_status: Mapped[str] = mapped_column(
        String(32), default=ValidityStatus.VALID.value, nullable=False
    )
    classification_status: Mapped[str] = mapped_column(
        String(32), default=ClassificationStatus.CLASSIFIED.value, nullable=False
    )
    validation_messages: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    technical_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    equivalence_key: Mapped[str | None] = mapped_column(String(128))
    equivalence_group_id: Mapped[uuid.UUID | None] = mapped_column()

    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_outlier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    matches_profile: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Итоговая причина исключения из агрегированного расчета.
    exclusion_reason: Mapped[str] = mapped_column(
        String(32), default=ExclusionReason.NONE.value, nullable=False
    )
    exclusion_detail: Mapped[str | None] = mapped_column(String(512))

    raw_object_ref: Mapped[str | None] = mapped_column(String(1024))
    raw_response_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_responses.id", ondelete="SET NULL")
    )
    html_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("html_snapshots.id", ondelete="SET NULL")
    )
    deeplink: Mapped[str | None] = mapped_column(String(2048))
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_version: Mapped[str | None] = mapped_column(String(32))
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    flight: Mapped[FlightOffer | None] = relationship(
        back_populates="offer", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    rail: Mapped[RailOffer | None] = relationship(
        back_populates="offer", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    accommodation: Mapped[AccommodationOffer | None] = relationship(
        back_populates="offer", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def offer_type_enum(self) -> OfferType:
        return OfferType(self.offer_type)

    @property
    def is_valid(self) -> bool:
        return self.validity_status == ValidityStatus.VALID.value

    @property
    def is_countable(self) -> bool:
        """Входит ли предложение в агрегированный расчет."""
        return self.exclusion_reason == ExclusionReason.NONE.value

    @property
    def detail(self) -> FlightOffer | RailOffer | AccommodationOffer | None:
        return self.flight or self.rail or self.accommodation


class FlightOffer(Base):
    """Специализированные поля авиапредложения (SCOPE-R P §4)."""

    __tablename__ = "flight_offers"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True
    )

    origin_code: Mapped[str | None] = mapped_column(String(8))
    destination_code: Mapped[str | None] = mapped_column(String(8))
    origin_name: Mapped[str | None] = mapped_column(String(255))
    destination_name: Mapped[str | None] = mapped_column(String(255))

    outbound_departure_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    outbound_arrival_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    inbound_departure_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    inbound_arrival_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    #: Сегменты плеч со всеми полями: перевозчик, номер рейса, времена, аэропорты.
    outbound_segments: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    inbound_segments: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    outbound_stops: Mapped[int | None] = mapped_column(Integer)
    inbound_stops: Mapped[int | None] = mapped_column(Integer)
    outbound_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    inbound_duration_minutes: Mapped[int | None] = mapped_column(Integer)

    marketing_carriers: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    flight_numbers: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    cabin_class: Mapped[str | None] = mapped_column(String(32))
    fare_family: Mapped[str | None] = mapped_column(String(128))
    baggage_type: Mapped[str] = mapped_column(String(16), nullable=False)
    baggage_raw: Mapped[str | None] = mapped_column(String(255))
    refundability: Mapped[str] = mapped_column(String(16), nullable=False)

    is_round_trip: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    passenger_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(32), default="ALL_PASSENGERS", nullable=False)

    offer: Mapped[Offer] = relationship(back_populates="flight")


class RailOffer(Base):
    """Специализированные поля ЖД-предложения. Плацкарт и купе агрегируются раздельно."""

    __tablename__ = "rail_offers"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True
    )

    origin_station_code: Mapped[str | None] = mapped_column(String(16))
    destination_station_code: Mapped[str | None] = mapped_column(String(16))
    origin_station_name: Mapped[str | None] = mapped_column(String(255))
    destination_station_name: Mapped[str | None] = mapped_column(String(255))
    origin_city_name: Mapped[str | None] = mapped_column(String(128))
    destination_city_name: Mapped[str | None] = mapped_column(String(128))

    outbound_train_number: Mapped[str | None] = mapped_column(String(32))
    inbound_train_number: Mapped[str | None] = mapped_column(String(32))
    outbound_departure_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    outbound_arrival_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    inbound_departure_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    inbound_arrival_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    outbound_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    inbound_duration_minutes: Mapped[int | None] = mapped_column(Integer)

    carriers: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    car_type: Mapped[str | None] = mapped_column(String(32))
    car_type_raw: Mapped[str | None] = mapped_column(String(64))
    service_classes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    available_places_outbound: Mapped[int | None] = mapped_column(Integer)
    available_places_inbound: Mapped[int | None] = mapped_column(Integer)
    is_two_storey: Mapped[bool | None] = mapped_column(Boolean)

    price_per_place_outbound: Mapped[Decimal | None] = mapped_column(Money)
    price_per_place_inbound: Mapped[Decimal | None] = mapped_column(Money)
    passenger_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_round_trip: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    refundability: Mapped[str] = mapped_column(String(16), nullable=False)
    segments: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    offer: Mapped[Offer] = relationship(back_populates="rail")


class AccommodationOffer(Base):
    """Специализированные поля предложения по проживанию (SCOPE-R P §5).

    Цена — стоимость одного подходящего номера за весь период, не на человека.
    """

    __tablename__ = "accommodation_offers"
    __table_args__ = (Index("ix_accommodation_property_key", "property_key"),)

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True
    )

    property_source_id: Mapped[str | None] = mapped_column(String(128))
    property_name: Mapped[str | None] = mapped_column(String(512))
    property_key: Mapped[str | None] = mapped_column(String(256))
    accommodation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    accommodation_type_raw: Mapped[str | None] = mapped_column(String(128))
    stars: Mapped[int | None] = mapped_column(Integer)
    stars_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    address: Mapped[str | None] = mapped_column(String(1024))
    city_name: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    check_in: Mapped[date | None] = mapped_column(Date)
    check_out: Mapped[date | None] = mapped_column(Date)
    nights: Mapped[int | None] = mapped_column(Integer)

    room_name: Mapped[str | None] = mapped_column(String(512))
    room_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_guests: Mapped[int | None] = mapped_column(Integer)
    capacity_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capacity_confirmation_basis: Mapped[str | None] = mapped_column(String(64))
    adults: Mapped[int | None] = mapped_column(Integer)
    children_ages: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    meal_raw: Mapped[str | None] = mapped_column(String(255))
    cancellation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    cancellation_raw: Mapped[str | None] = mapped_column(String(512))

    review_score: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    amenities: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    offer: Mapped[Offer] = relationship(back_populates="accommodation")
