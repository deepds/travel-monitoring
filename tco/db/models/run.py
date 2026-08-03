"""ScenarioRun — неизменяемый исторический результат применения методики."""

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

from tco.core.enums import ConfidenceLevel, RunStatus, RunType
from tco.db.base import Base, JSONB, Money, TZDateTime, UUIDPrimaryKeyMixin
from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot


class ScenarioRun(UUIDPrimaryKeyMixin, Base):
    """Результат расчета сценария по конкретному снимку и профилю.

    Immutable: после создания запись не изменяется. Новая версия профиля не
    пересчитывает старые run — она создает новые (SCOPE-R E §3, R §3).
    """

    __tablename__ = "scenario_runs"
    __table_args__ = (
        Index("ix_runs_scenario_time", "scenario_id", "started_at"),
        Index("ix_runs_snapshot", "market_snapshot_id"),
        Index("ix_runs_status_type", "status", "run_type"),
        Index("ix_runs_observation_date", "observation_date"),
        Index("ix_runs_cache_key", "cache_key"),
    )

    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_scenarios.id"), nullable=False)
    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL")
    )
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)

    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Разница между датой расчета и датой начала поездки.
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)

    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calculation_profiles.id"), nullable=False)
    profile_code: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    component_statuses: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    transport_p25: Mapped[Decimal | None] = mapped_column(Money)
    transport_median: Mapped[Decimal | None] = mapped_column(Money)
    transport_p75: Mapped[Decimal | None] = mapped_column(Money)
    transport_source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transport_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transport_disagreement: Mapped[float | None] = mapped_column(Float)

    hotel_p25: Mapped[Decimal | None] = mapped_column(Money)
    hotel_median: Mapped[Decimal | None] = mapped_column(Money)
    hotel_p75: Mapped[Decimal | None] = mapped_column(Money)
    hotel_source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hotel_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hotel_disagreement: Mapped[float | None] = mapped_column(Float)

    #: Итог рассчитывается только при наличии обоих компонентов.
    total_estimated_cost: Mapped[Decimal | None] = mapped_column(Money)
    total_p25: Mapped[Decimal | None] = mapped_column(Money)
    total_p75: Mapped[Decimal | None] = mapped_column(Money)
    price_per_person: Mapped[Decimal | None] = mapped_column(Money)
    transport_share: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    traveler_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    quality_score: Mapped[float | None] = mapped_column(Float)
    quality_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence_level: Mapped[str | None] = mapped_column(String(16))
    confidence_reason: Mapped[str | None] = mapped_column(String(1024))
    confidence_factors: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_codes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    valid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    excluded_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outlier_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Полная объяснимость: по источникам, исключениям, снижениям качества.
    explainability_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    cache_key: Mapped[str | None] = mapped_column(String(128))
    served_from_cache: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contains_synthetic_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_summary: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    job_id: Mapped[uuid.UUID | None] = mapped_column()
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)

    scenario: Mapped[TravelScenario] = relationship(lazy="joined")
    profile: Mapped[CalculationProfile] = relationship(lazy="joined")
    snapshot: Mapped[MarketSnapshot | None] = relationship(lazy="select")

    @property
    def status_enum(self) -> RunStatus:
        return RunStatus(self.status)

    @property
    def run_type_enum(self) -> RunType:
        return RunType(self.run_type)

    @property
    def confidence_level_enum(self) -> ConfidenceLevel | None:
        return ConfidenceLevel(self.confidence_level) if self.confidence_level else None

    @property
    def is_complete(self) -> bool:
        """Оба компонента рассчитаны — итоговая стоимость определена."""
        return self.total_estimated_cost is not None

    @property
    def counts_toward_kpi(self) -> bool:
        """Учет в KPI стабильности: SUCCESS либо PARTIAL_SUCCESS выше порога."""
        if self.status == RunStatus.SUCCESS.value:
            return True
        if self.status != RunStatus.PARTIAL_SUCCESS.value:
            return False
        threshold = float((self.quality_breakdown or {}).get("partial_success_threshold", 60.0))
        return (self.quality_score or 0.0) >= threshold
