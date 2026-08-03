"""Экспорт в CSV и XLSX (DELTA §6.14).

Выгрузка всегда соответствует выбранному фильтру и содержит поля качества,
статуса, источников и версии методики.
"""

from __future__ import annotations

import gzip
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from tco.api.deps import AnalystDep, SessionDep, SettingsDep, ViewerDep
from tco.api.dispatch import dispatch
from tco.api.routers.dashboard import FiltersDep
from tco.api.serializers import export_artifact
from tco.core.enums import AuditAction, ExportDataset, ExportFormat, JobStatus, JobType
from tco.core.errors import ConflictError, NotFoundError
from tco.core.logging import get_logger
from tco.core.utils import new_uuid, utcnow
from tco.db.models.job import ExportArtifact
from tco.services import audit
from tco.services import jobs as job_service
from tco.storage.raw_store import get_raw_store

logger = get_logger(__name__)

router = APIRouter(prefix="/exports", tags=["exports"])

_CONTENT_TYPES = {
    ExportFormat.CSV.value: "text/csv; charset=utf-8",
    ExportFormat.XLSX.value: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}


class ExportOptions(BaseModel):
    dataset: ExportDataset = ExportDataset.SCENARIO_RUNS
    export_format: Annotated[ExportFormat, Field(alias="format")] = ExportFormat.CSV
    limit: int = Field(100_000, ge=1, le=1_000_000)
    snapshot_id: str | None = Field(None, description="Для набора OFFERS")
    window_days: int = Field(30, ge=1, le=365, description="Для набора SOURCE_METRICS")

    model_config = {"populate_by_name": True}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить экспорт",
)
def create_export(
    options: ExportOptions,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AnalystDep,
    filters: FiltersDep,
) -> dict[str, Any]:
    """Ставит выгрузку в очередь и возвращает ``job_id``."""
    params = {
        "dataset": options.dataset.value,
        "format": options.export_format.value,
        "filters": filters.as_dict(),
        "limit": options.limit,
        "snapshot_id": options.snapshot_id,
        "window_days": options.window_days,
    }

    handle = job_service.create_job(
        session,
        job_type=JobType.EXPORT,
        # Экспорт не дедуплицируется: две одинаковые выгрузки — это
        # два законных запроса пользователя.
        idempotency_key=f"export:{new_uuid().hex}",
        params=params,
        created_by=principal.username,
        request_id=getattr(request.state, "request_id", None),
    )
    job = handle.job
    job_service.transition(session, job, JobStatus.QUEUED, message="Экспорт поставлен в очередь")

    audit.record(
        session,
        AuditAction.EXPORT,
        principal=principal,
        object_type="Job",
        object_id=str(job.id),
        summary=f"Запрошен экспорт {options.dataset.value} в {options.export_format.value}",
        payload=params,
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()

    from tco.tasks.maintenance import export_dataset

    dispatch(export_dataset, job=job, session=session, job_id=str(job.id))
    session.expire_all()
    job = job_service.get_job(session, job.id)

    return {
        "job_id": str(job.id),
        "status": job.status,
        "status_url": f"{settings.api_prefix}/exports/{job.id}",
        "download_url": f"{settings.api_prefix}/exports/{job.id}/download",
        "dataset": options.dataset.value,
        "format": options.export_format.value,
    }


@router.get("/{job_id}", summary="Состояние экспорта")
def get_export(
    job_id: str, session: SessionDep, settings: SettingsDep, _: ViewerDep
) -> dict[str, Any]:
    job = job_service.get_job(session, job_id)
    if job.job_type != JobType.EXPORT.value:
        raise NotFoundError(f"Задача {job_id} не является экспортом")

    payload = job_service.job_payload(job, settings.api_prefix)
    payload["status_url"] = f"{settings.api_prefix}/exports/{job.id}"

    artifact = session.scalars(
        select(ExportArtifact).where(ExportArtifact.job_id == job.id)
    ).first()
    if artifact is not None:
        payload["artifact"] = export_artifact(artifact)
        payload["download_url"] = f"{settings.api_prefix}/exports/{job.id}/download"
    return payload


@router.get("/{job_id}/download", summary="Скачать результат экспорта")
def download(job_id: str, session: SessionDep, _: ViewerDep) -> Response:
    """Отдает готовый файл выгрузки."""
    job = job_service.get_job(session, job_id)
    if job.job_type != JobType.EXPORT.value:
        raise NotFoundError(f"Задача {job_id} не является экспортом")

    artifact = session.scalars(
        select(ExportArtifact).where(ExportArtifact.job_id == job.id)
    ).first()
    if artifact is None:
        raise ConflictError(
            "Файл еще не готов",
            details={"status": job.status, "job_id": str(job.id)},
        )
    if artifact.expires_at and artifact.expires_at < utcnow():
        raise ConflictError(
            "Срок хранения выгрузки истек — сформируйте ее заново",
            details={"expires_at": artifact.expires_at.isoformat()},
        )

    try:
        data = get_raw_store().get_bytes(artifact.storage_ref)
    except FileNotFoundError as exc:
        raise NotFoundError("Файл выгрузки отсутствует в хранилище") from exc

    # Артефакт хранится сжатым; сеть получает распакованный файл.
    if artifact.storage_ref.endswith(".gz"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError):
            pass

    media_type = _CONTENT_TYPES.get(artifact.export_format, "application/octet-stream")
    logger.info(
        "Выгрузка скачана",
        job_id=str(job.id),
        dataset=artifact.dataset,
        rows=artifact.row_count,
    )
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Row-Count": str(artifact.row_count),
            "X-Checksum-Sha256": artifact.checksum_sha256 or "",
        },
    )
