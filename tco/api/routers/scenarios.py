"""Сценарии путешествия и импорт каталога (DELTA §6.3, §6.4)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Body, File, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from tco.api.deps import (
    AdminDep,
    AnalystDep,
    PaginationDep,
    SessionDep,
    ViewerDep,
    apply_sort,
    get_or_404,
)
from tco.api.serializers import scenario_brief, scenario_full
from tco.core.enums import (
    AccommodationType,
    AuditAction,
    CancellationFilter,
    FlightFareType,
    MealType,
    RailClass,
    ScenarioType,
    StarsFilter,
    TransportType,
)
from tco.core.errors import ConflictError, ValidationError
from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.reference import City
from tco.db.models.scenario import TravelScenario
from tco.services import audit
from tco.services.scenario_lifecycle import (
    purge_scenario,
    scenario_footprint,
    soft_delete_scenario,
)
from tco.services.scenarios import (
    ScenarioDraft,
    create_scenario,
    export_scenarios_csv,
    import_scenarios,
)

logger = get_logger(__name__)

router = APIRouter(tags=["scenarios"])

_SORTABLE = {
    "created_at": TravelScenario.created_at,
    "departure_date": TravelScenario.departure_date,
    "priority": TravelScenario.priority,
    "code": TravelScenario.code,
}

#: Предельный размер загружаемого каталога — защита от исчерпания памяти.
_MAX_IMPORT_BYTES = 5 * 1024 * 1024


class ScenarioCreate(BaseModel):
    """Параметры нового сценария.

    Конструктор управляемый: значения ограничены перечислениями платформы.
    """

    origin_city_code: str = Field(max_length=32, description="Код города отправления")
    destination_city_code: str = Field(max_length=32, description="Код города назначения")
    departure_date: date
    return_date: date
    adults: int = Field(2, ge=1, le=9)
    children_ages: list[int] = Field(default_factory=list, max_length=8)
    #: ``None`` — компонента сценарием не наблюдается. Хотя бы одна из двух
    #: обязана остаться: сценарий без компонент ничего не измеряет.
    transport_type: TransportType | None = TransportType.AVIA
    flight_fare_type: FlightFareType | None = FlightFareType.CHEAPEST
    rail_class: RailClass | None = None
    accommodation_type: AccommodationType | None = AccommodationType.HOTEL
    stars: StarsFilter = StarsFilter.ANY
    meal_type: MealType = MealType.ANY
    cancellation_filter: CancellationFilter = CancellationFilter.ANY
    scenario_type: ScenarioType = ScenarioType.MONITORING
    code: str | None = Field(None, max_length=96)
    name: str | None = Field(None, max_length=255)
    active_from: date | None = None
    active_until: date | None = None
    priority: int = Field(100, ge=0, le=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(None, max_length=2048)

    @model_validator(mode="after")
    def _check(self) -> ScenarioCreate:
        if self.origin_city_code.strip().upper() == self.destination_city_code.strip().upper():
            raise ValueError("Город отправления и город назначения должны различаться")
        if self.return_date <= self.departure_date:
            raise ValueError("Дата возвращения должна быть позже даты отправления")
        if any(age < 0 or age > 17 for age in self.children_ages):
            raise ValueError("Возраст ребенка должен быть в диапазоне 0–17")
        if self.transport_type is None and self.accommodation_type is None:
            raise ValueError(
                "Сценарий должен наблюдать хотя бы одну компоненту: транспорт или проживание"
            )
        if self.transport_type == TransportType.RAIL and self.rail_class is None:
            raise ValueError("Для железнодорожного транспорта требуется класс вагона")
        if self.transport_type == TransportType.AVIA and self.flight_fare_type is None:
            raise ValueError("Для авиаперелета требуется тарифный режим")
        return self

    def to_draft(self) -> ScenarioDraft:
        # Взаимоисключающие поля обнуляем: сценарий не комбинирует авиа и ЖД.
        return ScenarioDraft(
            origin_city_code=self.origin_city_code,
            destination_city_code=self.destination_city_code,
            departure_date=self.departure_date,
            return_date=self.return_date,
            adults=self.adults,
            children_ages=tuple(self.children_ages),
            transport_type=self.transport_type,
            flight_fare_type=(
                self.flight_fare_type if self.transport_type == TransportType.AVIA else None
            ),
            rail_class=(self.rail_class if self.transport_type == TransportType.RAIL else None),
            accommodation_type=self.accommodation_type,
            stars=self.stars,
            meal_type=self.meal_type,
            cancellation_filter=self.cancellation_filter,
            scenario_type=self.scenario_type,
            code=self.code,
            name=self.name,
            active_from=self.active_from,
            active_until=self.active_until,
            priority=self.priority,
            tags=list(self.tags),
            notes=self.notes,
        )


class ScenarioPatch(BaseModel):
    """Изменяемые поля сценария.

    Расчетные параметры (маршрут, даты, состав, транспорт, размещение)
    не изменяются: их изменение — это уже другой сценарий с другим отпечатком.
    """

    name: str | None = Field(None, max_length=255)
    is_active: bool | None = None
    active_from: date | None = None
    active_until: date | None = None
    priority: int | None = Field(None, ge=0, le=1000)
    tags: list[str] | None = Field(None, max_length=20)
    notes: str | None = Field(None, max_length=2048)
    calculation_profile_id: str | None = None


@router.get("/scenarios", summary="Список сценариев")
def list_scenarios(
    session: SessionDep,
    _: ViewerDep,
    page: PaginationDep,
    scenario_type: ScenarioType | None = None,
    origin: Annotated[str | None, Query(description="Код города отправления")] = None,
    destination: Annotated[str | None, Query(description="Код города назначения")] = None,
    transport_type: TransportType | None = None,
    accommodation_type: AccommodationType | None = None,
    is_active: bool | None = None,
    departure_from: date | None = None,
    departure_to: date | None = None,
    search: Annotated[str | None, Query(max_length=128, description="Поиск по коду и названию")] = None,
    include_deleted: bool = False,
    sort_by: str = "created_at",
    sort_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, Any]:
    stmt = select(TravelScenario)
    conditions = []

    if not include_deleted:
        conditions.append(TravelScenario.deleted_at.is_(None))
    if scenario_type:
        conditions.append(TravelScenario.scenario_type == scenario_type.value)
    if origin:
        conditions.append(
            TravelScenario.origin_city_id
            == select(City.id).where(City.code == origin).scalar_subquery()
        )
    if destination:
        conditions.append(
            TravelScenario.destination_city_id
            == select(City.id).where(City.code == destination).scalar_subquery()
        )
    if transport_type:
        conditions.append(TravelScenario.transport_type == transport_type.value)
    if accommodation_type:
        conditions.append(TravelScenario.accommodation_type == accommodation_type.value)
    if is_active is not None:
        conditions.append(TravelScenario.is_active.is_(is_active))
    if departure_from:
        conditions.append(TravelScenario.departure_date >= departure_from)
    if departure_to:
        conditions.append(TravelScenario.departure_date <= departure_to)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(TravelScenario.code.ilike(pattern) | TravelScenario.name.ilike(pattern))

    for condition in conditions:
        stmt = stmt.where(condition)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = apply_sort(stmt, TravelScenario, sort_by, sort_dir, _SORTABLE)
    rows = session.scalars(stmt.offset(page.offset).limit(page.limit)).all()

    return {
        "items": [scenario_brief(row) for row in rows],
        "meta": {
            "page": page.page,
            "page_size": page.page_size,
            "total": total,
            "total_pages": (total + page.page_size - 1) // page.page_size,
        },
    }


@router.post(
    "/scenarios",
    status_code=status.HTTP_201_CREATED,
    summary="Создать сценарий",
)
def create(
    payload: ScenarioCreate,
    request: Request,
    session: SessionDep,
    principal: AnalystDep,
    response: Response,
) -> dict[str, Any]:
    """Создает сценарий либо возвращает существующий с тем же отпечатком.

    Уникальная комбинация параметров имеет стабильный fingerprint, поэтому
    повторный вызов не плодит дубликаты и отвечает ``200 OK``.
    """
    scenario, created = create_scenario(
        session, payload.to_draft(), created_by=principal.username
    )
    if not created:
        response.status_code = status.HTTP_200_OK

    audit.record(
        session,
        AuditAction.SCENARIO_CREATE,
        principal=principal,
        object_type="TravelScenario",
        object_id=str(scenario.id),
        summary=f"{'Создан' if created else 'Возвращен существующий'} сценарий {scenario.code}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return {**scenario_full(scenario), "created": created}


@router.get("/scenarios/{scenario_id}", summary="Сценарий по идентификатору")
def get_scenario(scenario_id: str, session: SessionDep, _: ViewerDep) -> dict[str, Any]:
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    return scenario_full(scenario)


@router.patch("/scenarios/{scenario_id}", summary="Изменить сценарий")
def patch_scenario(
    scenario_id: str,
    payload: ScenarioPatch,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, Any]:
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    if scenario.is_deleted:
        raise ConflictError("Сценарий удален и не может быть изменен")

    changes = payload.model_dump(exclude_unset=True)
    if "calculation_profile_id" in changes and changes["calculation_profile_id"]:
        changes["calculation_profile_id"] = uuid.UUID(changes["calculation_profile_id"])
    for field_name, value in changes.items():
        setattr(scenario, field_name, value)
    scenario.updated_at = utcnow()

    audit.record(
        session,
        AuditAction.SCENARIO_UPDATE,
        principal=principal,
        object_type="TravelScenario",
        object_id=str(scenario.id),
        summary=f"Изменен сценарий {scenario.code}",
        payload={"changes": {k: str(v) for k, v in changes.items()}},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return scenario_full(scenario)


@router.post("/scenarios/{scenario_id}/activate", summary="Активировать сценарий")
def activate(
    scenario_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    return _set_active(session, scenario_id, True, principal, request)


@router.post("/scenarios/{scenario_id}/deactivate", summary="Деактивировать сценарий")
def deactivate(
    scenario_id: str, request: Request, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    return _set_active(session, scenario_id, False, principal, request)


def _set_active(
    session: SessionDep, scenario_id: str, active: bool, principal: Any, request: Request
) -> dict[str, Any]:
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    if scenario.is_deleted:
        raise ConflictError("Сценарий удален")
    scenario.is_active = active
    scenario.updated_at = utcnow()
    audit.record(
        session,
        AuditAction.SCENARIO_ACTIVATE if active else AuditAction.SCENARIO_DEACTIVATE,
        principal=principal,
        object_type="TravelScenario",
        object_id=str(scenario.id),
        summary=f"Сценарий {scenario.code} {'активирован' if active else 'деактивирован'}",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return scenario_full(scenario)


@router.get("/scenarios/{scenario_id}/footprint", summary="Что накоплено по сценарию")
def footprint(scenario_id: str, session: SessionDep, _: AdminDep) -> dict[str, Any]:
    """Объем накопленных данных — показывается перед удалением.

    Решение об уничтожении невосполнимой истории принимается с числами перед
    глазами, поэтому интерфейс запрашивает их до открытия диалога.
    """
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    return {"scenario_code": scenario.code, **scenario_footprint(session, scenario)}


@router.delete("/scenarios/{scenario_id}", summary="Удалить сценарий")
def delete_scenario(
    scenario_id: str,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    purge_data: Annotated[
        bool,
        Query(
            description=(
                "Уничтожить накопленные снимки рынка, расчеты и предложения. "
                "По умолчанию удаляется только запись каталога."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Мягкое удаление либо полное — вместе с историей наблюдений.

    По умолчанию исторические расчеты остаются неизменными: они относятся к
    прошлому, и их достоверность не зависит от того, наблюдаем ли мы маршрут
    дальше. ``purge_data`` уничтожает историю безвозвратно — источники не
    отдают цены задним числом.
    """
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    code = scenario.code

    if purge_data:
        stats = purge_scenario(session, scenario)
        audit.record(
            session,
            AuditAction.SCENARIO_DELETE,
            principal=principal,
            object_type="TravelScenario",
            object_id=scenario_id,
            summary=f"Удален сценарий {code} вместе с накопленными данными",
            payload={"purged": stats},
            request_id=getattr(request.state, "request_id", None),
        )
        session.commit()
        return {
            "success": True,
            "purged": True,
            "message": f"Сценарий {code} удален вместе с накопленными данными",
            "removed": stats,
        }

    if scenario.is_deleted:
        return {"success": True, "purged": False, "message": "Сценарий уже удален"}

    soft_delete_scenario(scenario)
    audit.record(
        session,
        AuditAction.SCENARIO_DELETE,
        principal=principal,
        object_type="TravelScenario",
        object_id=scenario_id,
        summary=f"Мягко удален сценарий {code}, накопленные данные сохранены",
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return {
        "success": True,
        "purged": False,
        "message": f"Сценарий {code} удален, накопленные данные сохранены",
        "deleted_at": scenario.deleted_at.isoformat(),
    }


@router.get("/scenarios/{scenario_id}/export", summary="Выгрузить сценарий в CSV каталога")
def export_one(scenario_id: str, session: SessionDep, _: ViewerDep) -> Response:
    scenario = get_or_404(session, TravelScenario, scenario_id, "Сценарий")
    content = export_scenarios_csv([scenario])
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{scenario.code}.csv"'},
    )


# --------------------------------------------------------------------------- #
# Импорт каталога (DELTA §6.4)
# --------------------------------------------------------------------------- #


@router.post(
    "/admin/scenarios/import",
    summary="Импортировать каталог сценариев",
    tags=["admin"],
)
async def import_catalog(
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    file: Annotated[UploadFile | None, File(description="Файл CSV или YAML")] = None,
    content: Annotated[str | None, Body(embed=True, description="Содержимое каталога")] = None,
    fmt: Annotated[str, Query(alias="format", pattern="^(csv|yaml|yml)$")] = "csv",
    activate: bool = True,
) -> dict[str, Any]:
    """Импорт CSV/YAML. Ошибочные строки не срывают импорт целиком.

    Выполняется синхронно: каталог MVP (порядка 100 сценариев) разбирается
    быстро и без внешних запросов.
    """
    if file is not None:
        raw = await file.read()
        if len(raw) > _MAX_IMPORT_BYTES:
            raise ValidationError(
                f"Файл больше допустимых {_MAX_IMPORT_BYTES // 1024 // 1024} МБ",
                details={"size_bytes": len(raw)},
            )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("Файл должен быть в кодировке UTF-8") from exc
        source_file = file.filename
        if file.filename and file.filename.lower().endswith((".yaml", ".yml")):
            fmt = "yaml"
    elif content is not None:
        text = content
        source_file = None
    else:
        raise ValidationError("Необходимо передать файл или содержимое каталога")

    report = import_scenarios(
        session,
        text,
        fmt=fmt,
        created_by=principal.username,
        source_file=source_file,
        activate=activate,
    )

    audit.record(
        session,
        AuditAction.SCENARIO_IMPORT,
        principal=principal,
        object_type="TravelScenario",
        summary=(
            f"Импорт каталога: создано {report.created}, обновлено {report.updated}, "
            f"пропущено {report.skipped}, ошибок {len(report.errors)}"
        ),
        payload={"source_file": source_file, "format": fmt},
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return report.as_dict()


@router.get("/admin/scenarios/export", summary="Выгрузить каталог сценариев", tags=["admin"])
def export_catalog(
    session: SessionDep,
    _: AdminDep,
    is_active: bool | None = None,
    scenario_type: ScenarioType | None = None,
) -> Response:
    stmt = select(TravelScenario).where(TravelScenario.deleted_at.is_(None))
    if is_active is not None:
        stmt = stmt.where(TravelScenario.is_active.is_(is_active))
    if scenario_type:
        stmt = stmt.where(TravelScenario.scenario_type == scenario_type.value)

    scenarios = session.scalars(stmt.order_by(TravelScenario.code)).all()
    content = export_scenarios_csv(scenarios)
    filename = f"scenarios-{utcnow():%Y%m%d-%H%M%S}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
