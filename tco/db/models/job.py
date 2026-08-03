"""Job Engine: задачи, их события и аудит административных действий."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tco.core.enums import JobStatus
from tco.db.base import Base, JSONB, TZDateTime, UUIDPrimaryKeyMixin


class Job(UUIDPrimaryKeyMixin, Base):
    """Фоновая задача.

    ``idempotency_key`` гарантирует, что повторный запуск не создаст дубль
    (DELTA §4.5). Ключ строится из fingerprint сценария, временного окна,
    версии профиля и типа запуска.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency"),
        Index("ix_jobs_status_type", "status", "job_type"),
        Index("ix_jobs_created", "created_at"),
        Index("ix_jobs_scenario", "scenario_id"),
        Index("ix_jobs_parent", "parent_job_id"),
    )

    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING.value, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)

    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("travel_scenarios.id"))
    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    scenario_run_id: Mapped[uuid.UUID | None] = mapped_column()
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))

    celery_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    queue: Mapped[str | None] = mapped_column(String(64))

    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(4096))

    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(String(512))

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)

    queued_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Дедлайн для детектора зависших задач (Celery Beat).
    heartbeat_at: Mapped[datetime | None] = mapped_column(TZDateTime, index=True)

    created_by: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    @property
    def status_enum(self) -> JobStatus:
        return JobStatus(self.status)

    @property
    def is_terminal(self) -> bool:
        return self.status_enum.is_terminal

    @property
    def progress_ratio(self) -> float | None:
        if not self.progress_total:
            return None
        return min(1.0, self.progress_current / self.progress_total)


class JobEvent(UUIDPrimaryKeyMixin, Base):
    """Хронология изменения состояния задачи — ``GET /jobs/{id}/events``."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_time", "job_id", "created_at"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(String(2048))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class ExportArtifact(UUIDPrimaryKeyMixin, Base):
    """Результат задачи экспорта."""

    __tablename__ = "export_artifacts"
    __table_args__ = (Index("ix_exports_job", "job_id"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    export_format: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    filters: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Аудит административных и значимых действий (SCOPE-R E §7)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_time", "created_at"),
        Index("ix_audit_actor", "actor_username", "created_at"),
        Index("ix_audit_object", "object_type", "object_id"),
    )

    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(64))
    actor_username: Mapped[str | None] = mapped_column(String(64))
    actor_role: Mapped[str | None] = mapped_column(String(16))

    object_type: Mapped[str | None] = mapped_column(String(48))
    object_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(1024))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
