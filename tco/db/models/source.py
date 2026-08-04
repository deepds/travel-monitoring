"""Источники данных, их метрики и Source Confidence."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tco.core.enums import SourceCategory, SourceProtocol, SourceStatus
from tco.db.base import Base, JSONB, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Подключенный источник данных.

    Коннекторы не принимают произвольные URL: базовый адрес берется только из
    конфигурации, а ``allowed_hosts`` служит allowlist-проверкой (SCOPE-R E §7).
    """

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("code", name="uq_sources_code"),
        Index("ix_sources_category_enabled", "category", "is_enabled"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), default=SourceProtocol.REST.value, nullable=False)
    #: Какие типы предложений поставляет источник: FLIGHT / RAIL / ACCOMMODATION.
    offer_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    #: Признаки (``OfferAttribute``), которых нет в контракте источника. Их
    #: отсутствие — известное свойство источника, а не сбой классификации.
    unreported_attributes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    qualification_status: Mapped[str] = mapped_column(
        String(16), default=SourceStatus.CANDIDATE.value, nullable=False
    )
    qualification_notes: Mapped[str | None] = mapped_column(String(4096))
    qualified_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Синтетический источник (песочница) — никогда не выдается за рыночные данные.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_credentials: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_hosts: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    #: Юридический статус и право хранения (Source Qualification).
    legal_status: Mapped[str | None] = mapped_column(String(64))
    storage_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    html_storage_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Технический горизонт (SCOPE-R C §7).
    min_supported_date: Mapped[date | None] = mapped_column(Date)
    max_supported_date: Mapped[date | None] = mapped_column(Date)
    booking_horizon_days: Mapped[int | None] = mapped_column(Integer)
    horizon_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    supported_city_codes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    connector_version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    #: Состояние circuit breaker.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    circuit_open_until: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error: Mapped[str | None] = mapped_column(String(1024))

    @property
    def category_enum(self) -> SourceCategory:
        return SourceCategory(self.category)

    @property
    def protocol_enum(self) -> SourceProtocol:
        return SourceProtocol(self.protocol)

    @property
    def is_usable(self) -> bool:
        """Источник допущен к сбору: включен и прошел квалификацию."""
        return self.is_enabled and self.qualification_status in (
            SourceStatus.APPROVED.value,
            SourceStatus.CONDITIONAL.value,
        )

    def supports_offer_type(self, offer_type: str) -> bool:
        return offer_type in (self.offer_types or [])

    def supports_date(self, value: date) -> bool:
        if self.min_supported_date and value < self.min_supported_date:
            return False
        if self.max_supported_date and value > self.max_supported_date:
            return False
        return True


class SourceMetric(UUIDPrimaryKeyMixin, Base):
    """Технические метрики одного обращения к источнику.

    Одна запись на пару (snapshot, source, offer_type) — основа для
    connector success rate, latency, valid ratio и Source Confidence.
    """

    __tablename__ = "source_metrics"
    __table_args__ = (
        Index("ix_source_metrics_source_time", "source_id", "observed_at"),
        Index("ix_source_metrics_snapshot", "market_snapshot_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL")
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("travel_scenarios.id"))
    offer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)

    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    raw_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normalized_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unclassified_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outlier_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required_field_completeness: Mapped[float | None] = mapped_column(Float)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1024))
    connector_version: Mapped[str | None] = mapped_column(String(32))

    source: Mapped[Source] = relationship(lazy="joined")

    @property
    def is_success(self) -> bool:
        from tco.core.enums import ConnectorOutcome

        return ConnectorOutcome(self.outcome).is_ok


class SourceConfidence(UUIDPrimaryKeyMixin, Base):
    """Долгосрочная степень доверия к источнику (DELTA §7).

    Не является ни техническим health, ни Quality Score конкретного расчета.
    """

    __tablename__ = "source_confidence"
    __table_args__ = (
        UniqueConstraint("source_id", "calculation_date", name="uq_source_confidence_day"),
        Index("ix_source_confidence_source", "source_id", "calculation_date"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    calculation_date: Mapped[date] = mapped_column(Date, nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Значения факторов и их вклад — обязательны для объяснимости.
    input_metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    factor_scores: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    manual_override: Mapped[float | None] = mapped_column(Float)
    override_reason: Mapped[str | None] = mapped_column(String(1024))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    overridden_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)

    source: Mapped[Source] = relationship(lazy="joined")

    @property
    def effective_score(self) -> float:
        return self.manual_override if self.manual_override is not None else self.score
