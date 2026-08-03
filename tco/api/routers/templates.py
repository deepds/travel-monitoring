"""Шаблоны сценариев (DELTA §6.5).

Шаблон — частично заполненный набор параметров для конструктора. Он не
является сценарием: пользователь дополняет маршрут и даты, после чего
создается обычный ``TravelScenario``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from tco.api.deps import AnalystDep, SessionDep, ViewerDep, get_or_404
from tco.api.serializers import scenario_full, template
from tco.core.enums import AuditAction, ScenarioType
from tco.core.errors import ValidationError
from tco.core.logging import get_logger
from tco.db.models.reference import ScenarioTemplate
from tco.services import audit
from tco.services.scenarios import ScenarioDraft, create_scenario, draft_from_row

logger = get_logger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


class InstantiateRequest(BaseModel):
    """Недостающие параметры, дополняющие шаблон."""

    origin_city_code: str | None = Field(None, max_length=32)
    destination_city_code: str | None = Field(None, max_length=32)
    departure_date: date | None = None
    return_date: date | None = None
    adults: int | None = Field(None, ge=1, le=9)
    children_ages: list[int] | None = Field(None, max_length=8)
    scenario_type: ScenarioType = ScenarioType.ON_DEMAND
    overrides: dict[str, Any] = Field(
        default_factory=dict, description="Прочие поля сценария, переопределяющие шаблон"
    )

    @model_validator(mode="after")
    def _check(self) -> InstantiateRequest:
        if self.departure_date and self.return_date and self.return_date <= self.departure_date:
            raise ValueError("Дата возвращения должна быть позже даты отправления")
        return self


@router.get("", summary="Список шаблонов")
def list_templates(session: SessionDep, _: ViewerDep, active_only: bool = True) -> dict[str, Any]:
    stmt = select(ScenarioTemplate).order_by(ScenarioTemplate.sort_order, ScenarioTemplate.name)
    if active_only:
        stmt = stmt.where(ScenarioTemplate.is_active.is_(True))
    items = [template(row) for row in session.scalars(stmt).all()]
    return {"items": items, "total": len(items)}


@router.get("/{template_id}", summary="Шаблон по идентификатору")
def get_template(template_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    item = _resolve(session, template_id)
    return template(item)


@router.post(
    "/{template_id}/instantiate",
    status_code=status.HTTP_201_CREATED,
    summary="Создать сценарий из шаблона",
)
def instantiate(
    template_id: str,
    payload: InstantiateRequest,
    request: Request,
    session: SessionDep,
    principal: AnalystDep,
) -> dict[str, Any]:
    """Разворачивает шаблон в сценарий.

    Значения шаблона служат основой, переданные поля их переопределяют.
    """
    item = _resolve(session, template_id)

    row: dict[str, Any] = dict(item.defaults or {})
    explicit = payload.model_dump(exclude_unset=True, exclude={"overrides", "scenario_type"})
    row.update({k: v for k, v in explicit.items() if v is not None})
    row.update(payload.overrides)

    if isinstance(row.get("children_ages"), list):
        row["children_ages"] = ";".join(str(age) for age in row["children_ages"])

    missing = [
        field
        for field in ("origin_city_code", "destination_city_code", "departure_date", "return_date")
        if not row.get(field)
    ]
    if missing:
        raise ValidationError(
            "Шаблон не содержит обязательных параметров — их нужно передать",
            details={"missing_fields": missing, "template": item.code},
        )

    draft: ScenarioDraft = draft_from_row(row, f"template:{item.code}")
    draft.scenario_type = payload.scenario_type

    scenario, created = create_scenario(session, draft, created_by=principal.username)

    audit.record(
        session,
        AuditAction.SCENARIO_CREATE,
        principal=principal,
        object_type="TravelScenario",
        object_id=str(scenario.id),
        summary=f"Сценарий {scenario.code} создан из шаблона {item.code}",
        payload={"template": item.code},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return {**scenario_full(scenario), "created": created, "template_code": item.code}


def _resolve(session: SessionDep, template_id: str) -> ScenarioTemplate:
    """Шаблон доступен и по UUID, и по короткому коду."""
    found = session.scalars(
        select(ScenarioTemplate).where(ScenarioTemplate.code == template_id)
    ).first()
    if found is not None:
        return found
    return get_or_404(session, ScenarioTemplate, template_id, "Шаблон")
