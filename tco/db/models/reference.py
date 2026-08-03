"""Справочники: города, пользователи."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tco.core.enums import UserRole
from tco.db.base import Base, JSONB, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin


class City(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Поддерживаемый город. Может быть и точкой отправления, и назначения."""

    __tablename__ = "cities"
    __table_args__ = (
        UniqueConstraint("code", name="uq_cities_code"),
        Index("ix_cities_active", "is_active"),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    country_code: Mapped[str] = mapped_column(String(2), default="RU", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)

    #: Идентификаторы города в каждом источнике: IATA, коды станций РЖД,
    #: geo_id Туту/Яндекса. Ключ — код источника.
    iata_codes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    rail_station_codes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_ids: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()

    #: Доступность видов транспорта из/в город (Южно-Сахалинск не имеет ЖД связи
    #: с материком — сценарии с RAIL для него невалидны).
    supports_avia: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_rail: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    def source_id(self, source_code: str, key: str) -> str | int | None:
        entry = self.source_ids.get(source_code) if isinstance(self.source_ids, dict) else None
        if isinstance(entry, dict):
            return entry.get(key)
        return None


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Локальный пользователь (режим DEPLOYMENT_MODE=LOCAL)."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=UserRole.VIEWER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    @property
    def role_enum(self) -> UserRole:
        return UserRole(self.role)


class ScenarioTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Предустановленный шаблон для заполнения конструктора (режим TEMPLATE)."""

    __tablename__ = "scenario_templates"
    __table_args__ = (UniqueConstraint("code", name="uq_scenario_templates_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024))
    #: Частично заполненные параметры сценария; пользователь дополняет остальное.
    defaults: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ResultCacheEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """PostgreSQL-backed Result Cache.

    Redis используется как быстрый слой, но БД остается источником истины:
    in-memory кэш недопустим как единственное решение при нескольких процессах
    (SCOPE-R C §9).
    """

    __tablename__ = "result_cache"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_result_cache_key"),
        Index("ix_result_cache_expires", "expires_at"),
    )

    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    scenario_run_id: Mapped[uuid.UUID | None] = mapped_column()
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
