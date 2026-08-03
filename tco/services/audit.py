"""Аудит значимых и административных действий (SCOPE-R E §7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from tco.core.enums import AuditAction
from tco.core.logging import get_logger, redact
from tco.core.security import Principal
from tco.core.utils import utcnow
from tco.db.models.job import AuditEvent

logger = get_logger(__name__)


def record(
    session: Session,
    action: AuditAction,
    *,
    principal: Principal | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditEvent:
    """Записывает событие аудита. Секреты из payload вычищаются."""
    event = AuditEvent(
        action=action.value,
        actor_user_id=principal.user_id if principal else None,
        actor_username=principal.username if principal else "system",
        actor_role=principal.role.value if principal else None,
        object_type=object_type,
        object_id=str(object_id) if object_id else None,
        summary=(summary or "")[:1024] or None,
        payload=redact(payload or {}),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:512] or None,
        created_at=utcnow(),
    )
    session.add(event)
    logger.info(
        "Аудит",
        action=action.value,
        actor=event.actor_username,
        object_type=object_type,
        object_id=event.object_id,
    )
    return event
