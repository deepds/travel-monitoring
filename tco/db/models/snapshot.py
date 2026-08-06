"""MarketSnapshot — главная первичная аналитическая сущность (DELTA §1)."""

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

from tco.core.enums import SnapshotStatus, SnapshotType
from tco.db.base import Base, JSONB, TZDateTime, UUIDPrimaryKeyMixin
from tco.db.models.scenario import TravelScenario


class MarketSnapshot(UUIDPrimaryKeyMixin, Base):
    """Состояние рынка для сценария в момент наблюдения — до применения методики.

    Один снимок может обслуживать несколько ``ScenarioRun`` с разными профилями,
    что делает возможным сравнение методик на одних и тех же данных и аудит.
    После перехода в терминальный статус снимок неизменяем.
    """

    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_market_snapshots_idempotency"),
        Index("ix_snapshots_scenario_time", "scenario_id", "observed_at"),
        Index("ix_snapshots_type_status", "snapshot_type", "status"),
        Index("ix_snapshots_observation_date", "observation_date"),
    )

    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("travel_scenarios.id"), nullable=False)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_type: Mapped[str] = mapped_column(String(24), nullable=False)

    requested_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Момент наблюдения рынка (окно расписания для плановых снимков).
    observed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Индекс внутридневного окна: 0..3 при интервале 6 часов.
    observation_slot: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), default=SnapshotStatus.COLLECTING.value, nullable=False)
    source_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_codes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    transport_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accommodation_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outlier_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    raw_response_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    html_snapshot_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_versions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: Сводка Source Confidence на момент снимка.
    source_confidence_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: Технические итоги по каждому источнику: outcome, latency, счетчики.
    collection_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: Параметры сценария на момент снимка (сценарий может быть изменен позже).
    scenario_params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error_summary: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    #: Содержит ли снимок предложения синтетического источника (песочница).
    contains_synthetic_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)
    offers_purged_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    scenario: Mapped[TravelScenario] = relationship(lazy="joined")

    @property
    def status_enum(self) -> SnapshotStatus:
        return SnapshotStatus(self.status)

    @property
    def snapshot_type_enum(self) -> SnapshotType:
        return SnapshotType(self.snapshot_type)

    @property
    def is_finalized(self) -> bool:
        return self.status in (
            SnapshotStatus.COMPLETE.value,
            SnapshotStatus.PARTIAL.value,
            SnapshotStatus.EMPTY.value,
            SnapshotStatus.FAILED.value,
        )

    @property
    def total_offer_count(self) -> int:
        return int(self.transport_offer_count) + int(self.accommodation_offer_count)

    @property
    def offers_available(self) -> bool:
        """Пригоден ли снимок для повторного расчета (offers не вычищены retention)."""
        return self.offers_purged_at is None


class SnapshotSourceResult(UUIDPrimaryKeyMixin, Base):
    """Результат обращения к одному источнику в рамках снимка.

    Дублирует часть ``SourceMetric``, но живет столько же, сколько метаданные
    снимка, и обеспечивает explainability даже после очистки offers.
    """

    __tablename__ = "snapshot_source_results"
    __table_args__ = (
        UniqueConstraint(
            "market_snapshot_id", "source_id", "offer_type", name="uq_snapshot_source_offer_type"
        ),
        Index("ix_snapshot_source_results_snapshot", "market_snapshot_id"),
    )

    market_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    offer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    raw_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normalized_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unclassified_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required_field_completeness: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1024))
    connector_version: Mapped[str | None] = mapped_column(String(32))
    collected_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    #: Фактический момент обращения к источнику — в отличие от
    #: ``collected_at``, который округлен до часа как метка окна наблюдения.
    #:
    #: Свежесть данных считается отсюда. От метки окна ее считать нельзя:
    #: суточный прогон сетки идет по лимиту темпа источников дольше двух часов,
    #: и снимок, помеченный 09:00 и закрытый в 11:05, объявлялся устаревшим на
    #: 125 минут при пороге 120 — хотя предложения в нем собраны минуту назад.
    #: Источник в таком снимке не допускался к расчету, и расчет выходил
    #: ``NO_DATA`` при полных данных: 799 расчетов из 1240 за один прогон.
    #:
    #: ``NULL`` у записей, сделанных до появления поля: у них свежесть
    #: по-прежнему считается от метки окна.
    fetched_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Выдача источника обрезана: обход страниц прекращен по бюджету времени
    #: либо уперся в потолок. Отличается от ошибки — обращение состоялось и
    #: предложения настоящие, но выборка неполна, и медиана по ней смещена.
    #: Нужен экрану «Покрытие и качество»: без пометки обрезанное наблюдение
    #: выглядит равноценным полному.
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
