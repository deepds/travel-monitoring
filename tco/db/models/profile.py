"""CalculationProfile — версионируемая методика расчета."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tco.core.enums import ProfileStatus
from tco.db.base import Base, JSONB, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin


class CalculationProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Профиль расчета.

    Жизненный цикл ``DRAFT → ACTIVE → ARCHIVED``. Только одна ACTIVE-версия на
    ``code``; ACTIVE неизменяем — изменение создает новую версию (SCOPE-R R §3).
    Все числовые пороги хранятся в ``rules`` и валидируются схемой
    ``tco.schemas.profile.ProfileRules``.
    """

    __tablename__ = "calculation_profiles"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_profile_code_version"),
        Index("ix_profiles_status", "status"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048))
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    version_seq: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ProfileStatus.DRAFT.value, nullable=False)

    #: Полный набор правил (фильтрация, eligibility, выбросы, агрегация,
    #: веса Quality Score, лимиты). Схема — ProfileRules.
    rules: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    activated_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    archived_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_by: Mapped[str | None] = mapped_column(String(64))

    @property
    def status_enum(self) -> ProfileStatus:
        return ProfileStatus(self.status)

    @property
    def is_active(self) -> bool:
        return self.status == ProfileStatus.ACTIVE.value

    @property
    def label(self) -> str:
        return f"{self.code}@{self.version}"
