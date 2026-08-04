"""Market Snapshot — главная первичная аналитическая сущность (DELTA §6.8).

Снимок неизменяем после завершения. Пересчет создает новый ``ScenarioRun``
и не трогает исходные данные, что позволяет сравнивать методики на одном и
том же наборе предложений.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tco.api.deps import (
    AnalystDep,
    PaginationDep,
    SessionDep,
    SettingsDep,
    ViewerDep,
    get_or_404,
)
from tco.api.dispatch import dispatch
from tco.api.serializers import (
    run_brief,
    snapshot_brief,
    snapshot_full,
    snapshot_source_result,
)
from tco.core.enums import (
    AuditAction,
    JobType,
    OfferType,
    SnapshotStatus,
    SnapshotType,
)
from tco.core.errors import ConflictError, NotFoundError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.raw import HtmlSnapshot, RawResponse
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot, SnapshotSourceResult
from tco.engine.fingerprint import job_idempotency_key
from tco.services import audit
from tco.services import jobs as job_service
from tco.services import offers as offer_service

logger = get_logger(__name__)

router = APIRouter(prefix="/market-snapshots", tags=["market-snapshots"])


class RecalculateRequest(BaseModel):
    """Пересчет снимка другим профилем (DELTA §9.3)."""

    profile_id: str | None = Field(None, description="Профиль; по умолчанию активный")
    profile_code: str | None = Field(None, description="Код профиля вместо идентификатора")


@router.get("", summary="Список снимков рынка")
def list_snapshots(
    session: SessionDep,
    _: ViewerDep,
    page: PaginationDep,
    scenario_id: str | None = None,
    scenario_code: str | None = None,
    snapshot_type: SnapshotType | None = None,
    snapshot_status: Annotated[SnapshotStatus | None, Query(alias="status")] = None,
    observation_date_from: date | None = None,
    observation_date_to: date | None = None,
    include_synthetic: bool = True,
) -> dict[str, Any]:
    stmt = select(MarketSnapshot)

    if scenario_id:
        stmt = stmt.where(MarketSnapshot.scenario_id == uuid.UUID(scenario_id))
    if scenario_code:
        stmt = stmt.where(
            MarketSnapshot.scenario_id
            == select(TravelScenario.id)
            .where(TravelScenario.code == scenario_code)
            .scalar_subquery()
        )
    if snapshot_type:
        stmt = stmt.where(MarketSnapshot.snapshot_type == snapshot_type.value)
    if snapshot_status:
        stmt = stmt.where(MarketSnapshot.status == snapshot_status.value)
    if observation_date_from:
        stmt = stmt.where(MarketSnapshot.observation_date >= observation_date_from)
    if observation_date_to:
        stmt = stmt.where(MarketSnapshot.observation_date <= observation_date_to)
    if not include_synthetic:
        stmt = stmt.where(MarketSnapshot.contains_synthetic_data.is_(False))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(MarketSnapshot.observed_at.desc()).offset(page.offset).limit(page.limit)
    ).all()

    return {
        "items": [snapshot_brief(row) for row in rows],
        "meta": {
            "page": page.page,
            "page_size": page.page_size,
            "total": total,
            "total_pages": (total + page.page_size - 1) // page.page_size,
        },
    }


@router.get("/{snapshot_id}", summary="Снимок рынка")
def get_snapshot(snapshot_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    snapshot = get_or_404(session, MarketSnapshot, snapshot_id, "Снимок")
    runs = session.scalars(
        select(ScenarioRun)
        .where(ScenarioRun.market_snapshot_id == snapshot.id)
        .order_by(ScenarioRun.created_at.desc())
    ).all()
    return {
        **snapshot_full(snapshot),
        # Один снимок может обслуживать несколько расчетов с разными профилями.
        "runs": [run_brief(run) for run in runs],
        "run_count": len(runs),
    }


@router.get("/{snapshot_id}/offers", summary="Предложения снимка")
def snapshot_offers(
    snapshot_id: str,
    session: SessionDep,
    _: ViewerDep,
    page: PaginationDep,
    offer_type: OfferType | None = None,
    source_code: str | None = None,
    valid_only: bool = False,
    exclude_outliers: bool = False,
) -> dict[str, Any]:
    """Нормализованные предложения, на которых построен расчет.

    Выбросы и невалидные записи сохраняются и помечаются — они видны в
    диагностике, но не участвуют в агрегированной оценке.
    """
    snapshot = get_or_404(session, MarketSnapshot, snapshot_id, "Снимок")
    if not snapshot.offers_available:
        raise ConflictError(
            "Предложения снимка удалены политикой retention",
            details={"offers_purged_at": snapshot.offers_purged_at.isoformat()},
        )

    return offer_service.paged_offers(
        session,
        snapshot,
        page,
        offer_type=offer_type,
        source_code=source_code,
        valid_only=valid_only,
        exclude_outliers=exclude_outliers,
    )


@router.get("/{snapshot_id}/sources", summary="Итоги по источникам снимка")
def snapshot_sources(snapshot_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    """Технические итоги обращения к каждому источнику.

    Живут столько же, сколько метаданные снимка, поэтому объяснимость
    сохраняется даже после очистки предложений по retention.
    """
    snapshot = get_or_404(session, MarketSnapshot, snapshot_id, "Снимок")
    rows = session.scalars(
        select(SnapshotSourceResult)
        .where(SnapshotSourceResult.market_snapshot_id == snapshot.id)
        .order_by(SnapshotSourceResult.source_code, SnapshotSourceResult.offer_type)
    ).all()
    return {
        "snapshot_id": str(snapshot.id),
        "items": [snapshot_source_result(row) for row in rows],
        "collection_summary": snapshot.collection_summary,
        "source_confidence_summary": snapshot.source_confidence_summary,
        "error_summary": list(snapshot.error_summary or []),
    }


@router.get("/{snapshot_id}/raw-artifacts", summary="Raw и HTML артефакты снимка")
def snapshot_artifacts(snapshot_id: str, session: SessionDep, _: AnalystDep) -> dict[str, Any]:
    """Ссылки на сохраненные исходные ответы и HTML-снимки.

    Возвращаются метаданные и ссылки хранилища, а не содержимое: выгрузка
    сырых ответов идет отдельным контролируемым каналом.
    """
    snapshot = get_or_404(session, MarketSnapshot, snapshot_id, "Снимок")

    raw_rows = session.scalars(
        select(RawResponse)
        .where(RawResponse.market_snapshot_id == snapshot.id)
        .order_by(RawResponse.collected_at)
    ).all()
    html_rows = session.scalars(
        select(HtmlSnapshot)
        .where(HtmlSnapshot.market_snapshot_id == snapshot.id)
        .order_by(HtmlSnapshot.collected_at)
    ).all()

    return {
        "snapshot_id": str(snapshot.id),
        "raw_responses": [
            {
                "id": str(row.id),
                "source_code": row.source_code,
                "offer_type": row.offer_type,
                "endpoint": row.endpoint,
                "http_status": row.http_status,
                "latency_ms": row.latency_ms,
                "storage_ref": row.storage_ref,
                "content_type": row.content_type,
                "content_encoding": row.content_encoding,
                "size_bytes": row.size_bytes,
                "checksum_sha256": row.checksum_sha256,
                "collected_at": row.collected_at.isoformat(),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "is_purged": row.is_purged,
            }
            for row in raw_rows
        ],
        "html_snapshots": [
            {
                "id": str(row.id),
                "source_code": row.source_code,
                "final_url": row.final_url,
                "http_status": row.http_status,
                "storage_ref": row.storage_ref,
                "screenshot_ref": row.screenshot_ref,
                "parser_version": row.parser_version,
                "content_encoding": row.content_encoding,
                "size_bytes": row.size_bytes,
                "checksum_sha256": row.checksum_sha256,
                "collected_at": row.collected_at.isoformat(),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "is_purged": row.is_purged,
            }
            for row in html_rows
        ],
        "counts": {"raw_responses": len(raw_rows), "html_snapshots": len(html_rows)},
    }


@router.post(
    "/{snapshot_id}/recalculate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Пересчитать снимок другим профилем",
)
def recalculate(
    snapshot_id: str,
    payload: RecalculateRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AnalystDep,
) -> dict[str, Any]:
    """Создает новый ``ScenarioRun`` по выбранному профилю.

    Исходный снимок остается неизменным — это основа сравнения методик
    и аудита (DELTA §1.4).
    """
    snapshot = get_or_404(session, MarketSnapshot, snapshot_id, "Снимок")
    if not snapshot.is_finalized:
        raise ConflictError(
            "Снимок еще собирается — пересчет доступен после завершения",
            details={"status": snapshot.status},
        )
    if not snapshot.offers_available:
        raise ConflictError(
            "Предложения снимка удалены политикой retention — пересчет невозможен",
            details={"offers_purged_at": snapshot.offers_purged_at.isoformat()},
        )

    profile = _resolve_profile(session, payload)

    handle = job_service.create_job(
        session,
        job_type=JobType.SNAPSHOT_REPLAY,
        idempotency_key=job_idempotency_key(
            fingerprint=snapshot.scenario_fingerprint,
            requested_at=utcnow(),
            bucket_hours=0,
            profile_version=profile.version,
            run_type="REPLAY",
            salt=str(snapshot.id),
        ),
        params={"snapshot_id": str(snapshot.id), "profile_id": str(profile.id)},
        scenario_id=snapshot.scenario_id,
        created_by=principal.username,
        request_id=getattr(request.state, "request_id", None),
    )
    job = handle.job
    job.market_snapshot_id = snapshot.id

    audit.record(
        session,
        AuditAction.SNAPSHOT_RECALCULATE,
        principal=principal,
        object_type="MarketSnapshot",
        object_id=str(snapshot.id),
        summary=f"Пересчет снимка профилем {profile.label}",
        payload={"profile": profile.label, "job_id": str(job.id)},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()

    if handle.created:
        from tco.tasks.pipeline import replay_snapshot_with_profile

        dispatch(
            replay_snapshot_with_profile,
            job=job,
            session=session,
            snapshot_id=str(snapshot.id),
            profile_id=str(profile.id),
            created_by=principal.username,
            job_id=str(job.id),
        )
        session.expire_all()
        job = job_service.get_job(session, job.id)

    return {
        "job_id": str(job.id),
        "status": job.status,
        "status_url": f"{settings.api_prefix}/jobs/{job.id}",
        "snapshot_id": str(snapshot.id),
        "profile": profile.label,
        "scenario_run_id": str(job.scenario_run_id) if job.scenario_run_id else None,
    }


def _resolve_profile(session: SessionDep, payload: RecalculateRequest) -> CalculationProfile:
    if payload.profile_id:
        profile = session.get(CalculationProfile, uuid.UUID(payload.profile_id))
        if profile is None:
            raise NotFoundError(f"Профиль {payload.profile_id} не найден")
        return profile
    if payload.profile_code:
        profile = session.scalars(
            select(CalculationProfile)
            .where(CalculationProfile.code == payload.profile_code)
            .order_by(CalculationProfile.version_seq.desc())
        ).first()
        if profile is None:
            raise NotFoundError(f"Профиль {payload.profile_code} не найден")
        return profile

    from tco.services.calculation import active_profile

    return active_profile(session)
