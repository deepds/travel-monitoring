"""Журнал аудита (DELTA §6.15)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from tco.api.deps import AdminDep, PaginationDep, SessionDep, get_or_404
from tco.api.serializers import audit_event
from tco.core.enums import AuditAction
from tco.db.models.job import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", summary="События аудита")
def list_events(
    session: SessionDep,
    _: AdminDep,
    page: PaginationDep,
    action: AuditAction | None = None,
    actor: Annotated[str | None, Query(description="Имя пользователя")] = None,
    object_type: str | None = None,
    object_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> dict[str, Any]:
    """Аудит административных и значимых действий.

    Доступен только администратору: журнал содержит сведения о том, кто и
    что менял в платформе.
    """
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action == action.value)
    if actor:
        stmt = stmt.where(AuditEvent.actor_username == actor)
    if object_type:
        stmt = stmt.where(AuditEvent.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditEvent.object_id == object_id)
    if created_after:
        stmt = stmt.where(AuditEvent.created_at >= created_after)
    if created_before:
        stmt = stmt.where(AuditEvent.created_at <= created_before)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(AuditEvent.created_at.desc()).offset(page.offset).limit(page.limit)
    ).all()

    return {
        "items": [audit_event(row) for row in rows],
        "meta": {
            "page": page.page,
            "page_size": page.page_size,
            "total": total,
            "total_pages": (total + page.page_size - 1) // page.page_size,
        },
    }


@router.get("/events/{event_id}", summary="Событие аудита")
def get_event(event_id: str, session: SessionDep, _: AdminDep) -> dict[str, Any]:
    event = get_or_404(session, AuditEvent, event_id, "Событие аудита")
    return audit_event(event)


@router.get("/actions", summary="Справочник действий аудита")
def actions(_: AdminDep) -> dict[str, Any]:
    return {"items": [action.value for action in AuditAction]}
