"""Сырой слой: ответы источников и HTML-снимки."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tco.db.base import Base, JSONB, TZDateTime, UUIDPrimaryKeyMixin


class RawResponse(UUIDPrimaryKeyMixin, Base):
    """Метаданные сохраненного исходного ответа источника.

    Само тело лежит в raw storage (файловое или S3/MinIO) — в БД только ссылка,
    checksum и технические характеристики запроса. Токены и заголовки
    авторизации не сохраняются.
    """

    __tablename__ = "raw_responses"
    __table_args__ = (
        Index("ix_raw_responses_snapshot", "market_snapshot_id"),
        Index("ix_raw_responses_source_time", "source_id", "collected_at"),
        Index("ix_raw_responses_expires", "expires_at"),
    )

    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL")
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("travel_scenarios.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    offer_type: Mapped[str] = mapped_column(String(24), nullable=False)

    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Параметры запроса после вычистки секретов.
    request_params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(512))
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    storage_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), default="application/json", nullable=False)
    content_encoding: Mapped[str] = mapped_column(String(32), default="gzip", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    collected_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    is_purged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    connector_version: Mapped[str | None] = mapped_column(String(32))


class HtmlSnapshot(UUIDPrimaryKeyMixin, Base):
    """HTML-снимок страницы для браузерных/HTML источников (DELTA §3).

    Обязателен, если извлечение шло из HTML и это разрешено юридическими
    условиями источника (``Source.html_storage_allowed``).
    """

    __tablename__ = "html_snapshots"
    __table_args__ = (
        Index("ix_html_snapshots_snapshot", "market_snapshot_id"),
        Index("ix_html_snapshots_expires", "expires_at"),
    )

    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL")
    )
    raw_response_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_responses.id", ondelete="SET NULL")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("travel_scenarios.id"))

    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    final_url: Mapped[str | None] = mapped_column(String(2048))
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_headers: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    storage_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    screenshot_ref: Mapped[str | None] = mapped_column(String(1024))
    extraction_log: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(32))

    content_encoding: Mapped[str] = mapped_column(String(32), default="gzip", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    collected_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    is_purged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
