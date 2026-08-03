"""Политика retention (DELTA §2.4).

``0`` дней означает «бессрочно». Все сроки конфигурируются через настройки.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from tco.core.config import Settings, get_settings
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.job import ExportArtifact
from tco.db.models.offer import Offer
from tco.db.models.raw import HtmlSnapshot, RawResponse
from tco.db.models.reference import ResultCacheEntry
from tco.db.models.snapshot import MarketSnapshot
from tco.storage.raw_store import RawStore, get_raw_store

logger = get_logger(__name__)


def expiry_for(days: int, base: datetime | None = None) -> datetime | None:
    """Дата истечения хранения. ``None`` — бессрочно."""
    if days <= 0:
        return None
    return (base or utcnow()) + timedelta(days=days)


def cleanup_expired_raw_data(
    session: Session,
    *,
    settings: Settings | None = None,
    raw_store: RawStore | None = None,
    limit: int = 5000,
) -> dict[str, int]:
    """Удаляет истекшие raw-ответы и HTML-снимки вместе с телами в хранилище."""
    settings = settings or get_settings()
    raw_store = raw_store or get_raw_store()
    now = utcnow()
    stats = {"raw_purged": 0, "html_purged": 0, "storage_errors": 0}

    raw_rows = session.scalars(
        select(RawResponse)
        .where(RawResponse.is_purged.is_(False), RawResponse.expires_at.is_not(None))
        .where(RawResponse.expires_at < now)
        .limit(limit)
    ).all()
    for row in raw_rows:
        if raw_store.delete(row.storage_ref):
            stats["raw_purged"] += 1
        else:
            stats["storage_errors"] += 1
        row.is_purged = True

    html_rows = session.scalars(
        select(HtmlSnapshot)
        .where(HtmlSnapshot.is_purged.is_(False), HtmlSnapshot.expires_at.is_not(None))
        .where(HtmlSnapshot.expires_at < now)
        .limit(limit)
    ).all()
    for row in html_rows:
        if raw_store.delete(row.storage_ref):
            stats["html_purged"] += 1
        else:
            stats["storage_errors"] += 1
        if row.screenshot_ref:
            raw_store.delete(row.screenshot_ref)
        row.is_purged = True

    logger.info("Очистка raw-данных завершена", **stats)
    return stats


def cleanup_expired_offers(
    session: Session, *, settings: Settings | None = None, limit: int = 200
) -> dict[str, int]:
    """Удаляет нормализованные предложения старых снимков.

    Метаданные снимка и ScenarioRun сохраняются бессрочно — удаляется только
    подробная выборка предложений. Снимок помечается ``offers_purged_at``,
    после чего повторный расчет по нему невозможен.
    """
    settings = settings or get_settings()
    if settings.retention_offers_days <= 0:
        return {"snapshots_purged": 0, "offers_deleted": 0}

    cutoff = utcnow() - timedelta(days=settings.retention_offers_days)
    snapshots = session.scalars(
        select(MarketSnapshot)
        .where(MarketSnapshot.offers_purged_at.is_(None))
        .where(MarketSnapshot.observed_at < cutoff)
        .limit(limit)
    ).all()

    deleted = 0
    for snapshot in snapshots:
        result = session.execute(delete(Offer).where(Offer.market_snapshot_id == snapshot.id))
        deleted += int(result.rowcount or 0)
        snapshot.offers_purged_at = utcnow()

    logger.info(
        "Очистка предложений завершена",
        snapshots_purged=len(snapshots),
        offers_deleted=deleted,
    )
    return {"snapshots_purged": len(snapshots), "offers_deleted": deleted}


def cleanup_expired_cache(session: Session) -> dict[str, int]:
    """Удаляет истекшие записи Result Cache."""
    result = session.execute(
        delete(ResultCacheEntry).where(ResultCacheEntry.expires_at < utcnow())
    )
    count = int(result.rowcount or 0)
    logger.info("Очистка кэша завершена", entries_deleted=count)
    return {"cache_entries_deleted": count}


def cleanup_expired_exports(
    session: Session, *, raw_store: RawStore | None = None
) -> dict[str, int]:
    """Удаляет истекшие файлы экспорта."""
    raw_store = raw_store or get_raw_store()
    rows = session.scalars(
        select(ExportArtifact)
        .where(ExportArtifact.expires_at.is_not(None))
        .where(ExportArtifact.expires_at < utcnow())
    ).all()
    for row in rows:
        raw_store.delete(row.storage_ref)
        session.delete(row)
    logger.info("Очистка экспортов завершена", exports_deleted=len(rows))
    return {"exports_deleted": len(rows)}


def deactivate_finished_scenarios(session: Session) -> dict[str, int]:
    """Автоматически деактивирует сценарии после ``active_until`` (SCOPE-R P §14)."""
    from tco.db.models.scenario import TravelScenario

    today = utcnow().date()
    result = session.execute(
        update(TravelScenario)
        .where(TravelScenario.is_active.is_(True))
        .where(TravelScenario.active_until.is_not(None))
        .where(TravelScenario.active_until < today)
        .values(is_active=False, updated_at=utcnow())
    )
    count = int(result.rowcount or 0)
    if count:
        logger.info("Сценарии деактивированы по окончании периода", count=count)
    return {"scenarios_deactivated": count}


def run_all_retention(session: Session, settings: Settings | None = None) -> dict[str, int]:
    """Полный цикл обслуживания хранилища."""
    settings = settings or get_settings()
    stats: dict[str, int] = {}
    stats.update(cleanup_expired_raw_data(session, settings=settings))
    stats.update(cleanup_expired_offers(session, settings=settings))
    stats.update(cleanup_expired_cache(session))
    stats.update(cleanup_expired_exports(session))
    stats.update(deactivate_finished_scenarios(session))
    return stats
