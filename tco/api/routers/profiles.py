"""Calculation Profiles — версионируемая методика (DELTA §6.12).

Жизненный цикл ``DRAFT → ACTIVE → ARCHIVED``. Активная версия неизменяема:
изменение методики создает новую версию, а исторические ``ScenarioRun``
не пересчитываются автоматически.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from tco.api.deps import AdminDep, AnalystDep, SessionDep, ViewerDep, get_or_404
from tco.api.serializers import profile_brief, profile_full
from tco.core.enums import AuditAction, ProfileStatus
from tco.core.errors import ConflictError, ProfileImmutableError, ValidationError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.run import ScenarioRun
from tco.schemas.profile import ProfileCreate, ProfileRules
from tco.services import audit

logger = get_logger(__name__)

router = APIRouter(prefix="/calculation-profiles", tags=["calculation-profiles"])


class ProfileClone(BaseModel):
    """Клонирование профиля в новую DRAFT-версию."""

    name: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=2048)
    version: str | None = Field(None, max_length=32)
    rules: ProfileRules | None = None


class ProfileUpdate(BaseModel):
    """Изменение DRAFT-профиля. Для ACTIVE запрещено."""

    name: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=2048)
    rules: ProfileRules | None = None


@router.get("", summary="Список профилей расчета")
def list_profiles(
    session: SessionDep,
    _: ViewerDep,
    profile_status: ProfileStatus | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    stmt = select(CalculationProfile).order_by(
        CalculationProfile.code, CalculationProfile.version_seq.desc()
    )
    if profile_status:
        stmt = stmt.where(CalculationProfile.status == profile_status.value)
    if code:
        stmt = stmt.where(CalculationProfile.code == code)

    rows = session.scalars(stmt).all()
    return {"items": [profile_brief(row) for row in rows], "total": len(rows)}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Создать профиль")
def create_profile(
    payload: ProfileCreate,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Создает новую версию профиля в статусе ``DRAFT``.

    Версия рассчитывается автоматически, если не задана явно.
    """
    existing = session.scalars(
        select(CalculationProfile)
        .where(CalculationProfile.code == payload.code)
        .order_by(CalculationProfile.version_seq.desc())
    ).all()

    version_seq = (existing[0].version_seq + 1) if existing else 1
    version = payload.version or f"{version_seq}.0"

    if any(item.version == version for item in existing):
        raise ConflictError(
            f"Версия {version} профиля {payload.code} уже существует",
            details={"code": payload.code, "version": version},
        )

    profile = CalculationProfile(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        version=version,
        version_seq=version_seq,
        status=ProfileStatus.DRAFT.value,
        rules=payload.rules.model_dump(mode="json"),
        created_by=principal.username,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(profile)
    session.flush()

    audit.record(
        session,
        AuditAction.PROFILE_CREATE,
        principal=principal,
        object_type="CalculationProfile",
        object_id=str(profile.id),
        summary=f"Создан профиль {profile.label}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    logger.info("Создан профиль расчета", profile=profile.label, actor=principal.username)
    return profile_full(profile)


@router.get("/{profile_id}", summary="Профиль расчета")
def get_profile(profile_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    profile = get_or_404(session, CalculationProfile, profile_id, "Профиль")
    run_count = session.scalar(
        select(ScenarioRun.id).where(ScenarioRun.profile_id == profile.id).limit(1)
    )
    return {**profile_full(profile), "has_runs": run_count is not None}


@router.patch("/{profile_id}", summary="Изменить DRAFT-профиль")
def update_profile(
    profile_id: str,
    payload: ProfileUpdate,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Изменять можно только черновик: активная версия неизменяема."""
    profile = get_or_404(session, CalculationProfile, profile_id, "Профиль")
    if profile.status != ProfileStatus.DRAFT.value:
        raise ProfileImmutableError(
            f"Профиль в статусе {profile.status} неизменяем — создайте новую версию",
            details={"status": profile.status, "profile": profile.label},
        )

    changes = payload.model_dump(exclude_unset=True)
    if "rules" in changes and payload.rules is not None:
        profile.rules = payload.rules.model_dump(mode="json")
        changes["rules"] = "обновлены"
    if payload.name is not None:
        profile.name = payload.name
    if payload.description is not None:
        profile.description = payload.description
    profile.updated_at = utcnow()

    audit.record(
        session,
        AuditAction.PROFILE_CREATE,
        principal=principal,
        object_type="CalculationProfile",
        object_id=str(profile.id),
        summary=f"Изменен черновик профиля {profile.label}",
        payload={"changes": list(changes)},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return profile_full(profile)


@router.post("/{profile_id}/activate", summary="Активировать профиль")
def activate(
    profile_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    """Делает версию активной, архивируя предыдущую активную того же кода.

    Активной может быть только одна версия профиля.
    """
    profile = get_or_404(session, CalculationProfile, profile_id, "Профиль")
    if profile.status == ProfileStatus.ACTIVE.value:
        return profile_full(profile)
    if profile.status == ProfileStatus.ARCHIVED.value:
        raise ConflictError(
            "Архивный профиль нельзя активировать — склонируйте его в новую версию",
            details={"profile": profile.label},
        )

    # Правила обязаны быть валидны на момент активации.
    try:
        ProfileRules.parse(profile.rules)
    except Exception as exc:  # noqa: BLE001 — сообщение отдается пользователю
        raise ValidationError(
            "Правила профиля не соответствуют схеме и не могут быть активированы",
            details={"error": str(exc)[:500]},
        ) from exc

    now = utcnow()
    previous = session.scalars(
        select(CalculationProfile)
        .where(CalculationProfile.code == profile.code)
        .where(CalculationProfile.status == ProfileStatus.ACTIVE.value)
    ).all()
    for item in previous:
        item.status = ProfileStatus.ARCHIVED.value
        item.archived_at = now
        item.updated_at = now

    profile.status = ProfileStatus.ACTIVE.value
    profile.activated_at = now
    profile.updated_at = now

    audit.record(
        session,
        AuditAction.PROFILE_ACTIVATE,
        principal=principal,
        object_type="CalculationProfile",
        object_id=str(profile.id),
        summary=f"Активирован профиль {profile.label}",
        payload={"archived": [item.label for item in previous]},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    logger.info(
        "Профиль активирован",
        profile=profile.label,
        archived=[item.label for item in previous],
        actor=principal.username,
    )
    return profile_full(profile)


@router.post("/{profile_id}/archive", summary="Архивировать профиль")
def archive(
    profile_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    profile = get_or_404(session, CalculationProfile, profile_id, "Профиль")
    if profile.status == ProfileStatus.ARCHIVED.value:
        return profile_full(profile)

    if profile.status == ProfileStatus.ACTIVE.value:
        remaining = session.scalars(
            select(CalculationProfile)
            .where(CalculationProfile.status == ProfileStatus.ACTIVE.value)
            .where(CalculationProfile.id != profile.id)
        ).all()
        if not remaining:
            raise ConflictError(
                "Нельзя архивировать единственный активный профиль — "
                "сначала активируйте другую версию",
                details={"profile": profile.label},
            )

    now = utcnow()
    profile.status = ProfileStatus.ARCHIVED.value
    profile.archived_at = now
    profile.updated_at = now

    audit.record(
        session,
        AuditAction.PROFILE_ARCHIVE,
        principal=principal,
        object_type="CalculationProfile",
        object_id=str(profile.id),
        summary=f"Архивирован профиль {profile.label}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return profile_full(profile)


@router.post(
    "/{profile_id}/clone",
    status_code=status.HTTP_201_CREATED,
    summary="Склонировать профиль в новую версию",
)
def clone(
    profile_id: str,
    payload: ProfileClone,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Создает новую ``DRAFT``-версию на основе существующей.

    Это штатный способ изменить методику: активная версия остается нетронутой,
    а старые расчеты сохраняют ссылку на свою версию профиля.
    """
    source = get_or_404(session, CalculationProfile, profile_id, "Профиль")

    siblings = session.scalars(
        select(CalculationProfile)
        .where(CalculationProfile.code == source.code)
        .order_by(CalculationProfile.version_seq.desc())
    ).all()
    version_seq = (siblings[0].version_seq + 1) if siblings else 1
    version = payload.version or f"{version_seq}.0"
    if any(item.version == version for item in siblings):
        raise ConflictError(
            f"Версия {version} профиля {source.code} уже существует",
            details={"code": source.code, "version": version},
        )

    rules = payload.rules.model_dump(mode="json") if payload.rules else dict(source.rules or {})
    now = utcnow()
    clone_profile = CalculationProfile(
        code=source.code,
        name=payload.name or source.name,
        description=payload.description or source.description,
        version=version,
        version_seq=version_seq,
        status=ProfileStatus.DRAFT.value,
        rules=rules,
        created_by=principal.username,
        created_at=now,
        updated_at=now,
    )
    session.add(clone_profile)
    session.flush()

    audit.record(
        session,
        AuditAction.PROFILE_CLONE,
        principal=principal,
        object_type="CalculationProfile",
        object_id=str(clone_profile.id),
        summary=f"Профиль {source.label} склонирован в {clone_profile.label}",
        payload={"source_profile": source.label},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return profile_full(clone_profile)


@router.get("/{profile_id}/rules-schema", summary="JSON Schema правил профиля")
def rules_schema(profile_id: str, session: SessionDep, _: AnalystDep) -> dict[str, Any]:
    """Схема правил — контракт для редактора профиля в UI."""
    get_or_404(session, CalculationProfile, profile_id, "Профиль")
    return ProfileRules.model_json_schema()
