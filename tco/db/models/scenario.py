"""TravelScenario — объект наблюдения платформы."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    MealType,
    ScenarioType,
    StarsFilter,
    TransportType,
)
from tco.db.base import Base, JSONB, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin
from tco.db.models.reference import City


class TravelScenario(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Уникальная комбинация параметров путешествия.

    Каждая уникальная комбинация — отдельный сценарий (SCOPE-R S §3.4).
    ``fingerprint`` вычисляется из бизнес-параметров и гарантирует стабильность
    идентификации сценария между импортами.
    """

    __tablename__ = "travel_scenarios"
    __table_args__ = (
        UniqueConstraint("code", name="uq_travel_scenarios_code"),
        Index("ix_scenarios_fingerprint", "fingerprint"),
        Index("ix_scenarios_active_type", "is_active", "scenario_type"),
        Index("ix_scenarios_route", "origin_city_id", "destination_city_id"),
        Index("ix_scenarios_departure", "departure_date"),
        Index("ix_scenarios_showcase_grid", "is_showcase_grid"),
    )

    code: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario_type: Mapped[str] = mapped_column(
        String(16), default=ScenarioType.MONITORING.value, nullable=False
    )

    origin_city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"), nullable=False)
    destination_city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"), nullable=False)

    departure_date: Mapped[date] = mapped_column(Date, nullable=False)
    return_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Денормализовано намеренно: используется в агрегатах и экспорте.
    nights: Mapped[int] = mapped_column(Integer, nullable=False)

    adults: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    #: Массив возрастов детей поддерживается моделью данных даже при одном
    #: семейном шаблоне в UI (SCOPE-R C §4).
    children_ages: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    #: ``NULL`` — компонента сценарием не наблюдается. Хотя бы одна из двух
    #: обязана быть заполнена: сценарий без компонент ничего не измеряет.
    transport_type: Mapped[str | None] = mapped_column(String(8))
    flight_fare_type: Mapped[str | None] = mapped_column(String(24))
    rail_class: Mapped[str | None] = mapped_column(String(24))

    accommodation_type: Mapped[str | None] = mapped_column(String(24))
    stars: Mapped[str] = mapped_column(String(16), default=StarsFilter.ANY.value, nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), default=MealType.ANY.value, nullable=False)
    cancellation_filter: Mapped[str] = mapped_column(
        String(24), default=CancellationFilter.ANY.value, nullable=False
    )

    calculation_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calculation_profiles.id")
    )

    #: Сценарий заведен скользящей сеткой витрины, а не каталогом наблюдения.
    #:
    #: Поле, а не тег: по тегу нельзя отобрать в SQL переносимо, а сетка должна
    #: отсекаться на стороне СУБД — иначе она попадает в агрегаты рынка, где ее
    #: односоставные расчеты обваливают медиану. В отпечаток не входит:
    #: принадлежность к сетке не меняет наблюдаемую поездку.
    is_showcase_grid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    active_from: Mapped[date | None] = mapped_column(Date)
    active_until: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2048))
    source_file: Mapped[str | None] = mapped_column(String(255))

    origin_city: Mapped[City] = relationship(foreign_keys=[origin_city_id], lazy="joined")
    destination_city: Mapped[City] = relationship(foreign_keys=[destination_city_id], lazy="joined")

    # ------------------------------------------------------------------ #
    # Производные свойства
    # ------------------------------------------------------------------ #

    @property
    def traveler_count(self) -> int:
        return int(self.adults) + len(self.children_ages or [])

    @property
    def transport_type_enum(self) -> TransportType | None:
        return TransportType(self.transport_type) if self.transport_type else None

    @property
    def accommodation_type_enum(self) -> AccommodationType | None:
        return AccommodationType(self.accommodation_type) if self.accommodation_type else None

    @property
    def observes_transport(self) -> bool:
        return self.transport_type is not None

    @property
    def observes_accommodation(self) -> bool:
        return self.accommodation_type is not None

    @property
    def stars_enum(self) -> StarsFilter:
        return StarsFilter(self.stars)

    @property
    def meal_type_enum(self) -> MealType:
        return MealType(self.meal_type)

    @property
    def cancellation_filter_enum(self) -> CancellationFilter:
        return CancellationFilter(self.cancellation_filter)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def is_active_on(self, on_date: date) -> bool:
        """Сценарий автоматически деактивируется после ``active_until``."""
        if not self.is_active or self.is_deleted:
            return False
        if self.active_from and on_date < self.active_from:
            return False
        if self.active_until and on_date > self.active_until:
            return False
        return True

    def business_params(self) -> dict:
        """Параметры, входящие в fingerprint и cache key (SCOPE-R P §13)."""
        return {
            "origin_city_id": str(self.origin_city_id),
            "destination_city_id": str(self.destination_city_id),
            "departure_date": self.departure_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "adults": int(self.adults),
            "children_ages": sorted(int(a) for a in (self.children_ages or [])),
            "transport_type": self.transport_type,
            "flight_fare_type": self.flight_fare_type,
            "rail_class": self.rail_class,
            "accommodation_type": self.accommodation_type,
            "stars": self.stars,
            "meal_type": self.meal_type,
            "cancellation_filter": self.cancellation_filter,
        }
