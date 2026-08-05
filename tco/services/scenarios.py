"""Работа со сценариями: создание, обновление, отпечаток, импорт из CSV/YAML."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    FlightFareType,
    MealType,
    RailClass,
    ScenarioType,
    StarsFilter,
    TransportType,
)
from tco.core.errors import ConflictError, NotFoundError, ValidationError
from tco.core.logging import get_logger
from tco.core.utils import parse_date, utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City
from tco.db.models.scenario import TravelScenario
from tco.engine.fingerprint import ScenarioKey, scenario_fingerprint

logger = get_logger(__name__)

#: Колонки CSV-каталога сценариев мониторинга.
CSV_COLUMNS = [
    "code",
    "name",
    "origin_city_code",
    "destination_city_code",
    "departure_date",
    "return_date",
    "adults",
    "children_ages",
    "transport_type",
    "flight_fare_type",
    "rail_class",
    "accommodation_type",
    "stars",
    "meal_type",
    "cancellation_filter",
    "active_from",
    "active_until",
    "priority",
    "tags",
    "notes",
]

#: Тег суточной частоты наблюдения: помеченные им сценарии собираются раз в
#: сутки, остальные — по общему интервалу.
#:
#: Частота выражена тегом, а не полем сценария, потому что она не описывает
#: наблюдаемую поездку. Полем она вошла бы в отпечаток, и смена частоты
#: создавала бы новый сценарий, разрывая историю наблюдений.
DAILY_CADENCE_TAG = "cadence:daily"


@dataclass(slots=True)
class ScenarioDraft:
    """Проверенный набор параметров для создания сценария.

    ``transport_type`` или ``accommodation_type`` могут быть ``None`` —
    компонента тогда не наблюдается. Хотя бы одна должна остаться: сценарий
    без компонент ничего не измеряет.
    """

    origin_city_code: str
    destination_city_code: str
    departure_date: date
    return_date: date
    adults: int = 2
    children_ages: tuple[int, ...] = ()
    transport_type: TransportType | None = TransportType.AVIA
    flight_fare_type: FlightFareType | None = FlightFareType.CHEAPEST
    rail_class: RailClass | None = None
    accommodation_type: AccommodationType | None = AccommodationType.HOTEL
    stars: StarsFilter = StarsFilter.ANY
    meal_type: MealType = MealType.ANY
    cancellation_filter: CancellationFilter = CancellationFilter.ANY
    scenario_type: ScenarioType = ScenarioType.MONITORING
    code: str | None = None
    name: str | None = None
    active_from: date | None = None
    active_until: date | None = None
    priority: int = 100
    tags: list[str] = field(default_factory=list)
    notes: str | None = None
    source_file: str | None = None


@dataclass(slots=True)
class ImportIssue:
    row: int
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "code": self.code, "message": self.message}


@dataclass(slots=True)
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[ImportIssue] = field(default_factory=list)
    created_codes: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.created + self.updated + self.skipped + len(self.errors)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "error_count": len(self.errors),
            "errors": [issue.as_dict() for issue in self.errors],
            "created_codes": self.created_codes,
            "total_processed": self.total_processed,
        }


# --------------------------------------------------------------------------- #
# Создание сценария
# --------------------------------------------------------------------------- #


def build_key(draft: ScenarioDraft) -> ScenarioKey:
    return ScenarioKey(
        origin_city_code=draft.origin_city_code,
        destination_city_code=draft.destination_city_code,
        departure_date=draft.departure_date,
        return_date=draft.return_date,
        adults=draft.adults,
        children_ages=tuple(draft.children_ages),
        transport_type=draft.transport_type,
        flight_fare_type=draft.flight_fare_type,
        rail_class=draft.rail_class,
        accommodation_type=draft.accommodation_type,
        stars=draft.stars,
        meal_type=draft.meal_type,
        cancellation_filter=draft.cancellation_filter,
    )


def default_code(draft: ScenarioDraft, fingerprint: str) -> str:
    """Читаемый код сценария, устойчивый к повторной генерации.

    Ненаблюдаемая компонента обозначается ``NONE``: по коду должно быть видно,
    что сценарий следит только за одной частью поездки.
    """
    if draft.transport_type is None:
        transport_part = "NONE"
    else:
        prefix = "AV" if draft.transport_type == TransportType.AVIA else "RW"
        variant = (
            (draft.flight_fare_type.value[:3] if draft.flight_fare_type else "STD")
            if draft.transport_type == TransportType.AVIA
            else (draft.rail_class.value[:3] if draft.rail_class else "STD")
        )
        transport_part = f"{prefix}{variant}"

    if draft.accommodation_type is None:
        stay_part = "NONE"
    else:
        stars = draft.stars.value if draft.stars != StarsFilter.ANY else "X"
        stay_part = f"{draft.accommodation_type.value[:4]}{stars}"

    return (
        f"{draft.origin_city_code}-{draft.destination_city_code}-"
        f"{draft.departure_date:%Y%m%d}-{transport_part}-{stay_part}-{fingerprint[:6]}"
    ).upper()


def default_name(draft: ScenarioDraft, origin: City, destination: City) -> str:
    nights = (draft.return_date - draft.departure_date).days
    composition = f"{draft.adults} взр."
    if draft.children_ages:
        composition += f" + {len(draft.children_ages)} реб."

    if draft.transport_type is None:
        scope = "только проживание"
    elif draft.accommodation_type is None:
        scope = "только авиа" if draft.transport_type == TransportType.AVIA else "только ЖД"
    else:
        scope = "авиа" if draft.transport_type == TransportType.AVIA else "ЖД"

    return (
        f"{origin.name} → {destination.name}, {draft.departure_date:%d.%m.%Y}, "
        f"{nights} ноч., {scope}, {composition}"
    )


def _code_taken(session: Session, code: str) -> bool:
    """Код занят и мягко удаленным сценарием: ограничение уникальности общее."""
    return (
        session.scalars(select(TravelScenario).where(TravelScenario.code == code)).first()
        is not None
    )


def create_scenario(
    session: Session,
    draft: ScenarioDraft,
    *,
    profile: CalculationProfile | None = None,
    created_by: str | None = None,
    allow_existing: bool = True,
) -> tuple[TravelScenario, bool]:
    """Создает сценарий или возвращает существующий с тем же отпечатком.

    Возвращает ``(сценарий, создан_ли)``.
    """
    origin = session.scalars(select(City).where(City.code == draft.origin_city_code)).first()
    destination = session.scalars(
        select(City).where(City.code == draft.destination_city_code)
    ).first()
    if origin is None:
        raise NotFoundError(f"Город отправления {draft.origin_city_code} не найден")
    if destination is None:
        raise NotFoundError(f"Город назначения {draft.destination_city_code} не найден")

    fingerprint = scenario_fingerprint(build_key(draft))
    existing = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.fingerprint == fingerprint)
        .where(TravelScenario.scenario_type == draft.scenario_type.value)
        .where(TravelScenario.deleted_at.is_(None))
    ).first()
    if existing is not None:
        if not allow_existing:
            raise ConflictError(
                f"Сценарий с такими параметрами уже существует: {existing.code}",
                details={"scenario_id": str(existing.id), "code": existing.code},
            )
        return existing, False

    # Мягко удаленный сценарий сохраняет свой код, поэтому повторное создание
    # с теми же параметрами обязано подобрать свободный код. Суффикс выводится
    # из отпечатка и сам по себе не уникален: без цикла третий цикл
    # «создать — удалить — создать» нарушал бы uq_travel_scenarios_code.
    code = draft.code or default_code(draft, fingerprint)
    if _code_taken(session, code):
        base = f"{code}-{fingerprint[6:10].upper()}"
        code = base
        attempt = 2
        while _code_taken(session, code):
            code = f"{base}-{attempt}"
            attempt += 1

    scenario = TravelScenario(
        code=code,
        name=draft.name or default_name(draft, origin, destination),
        scenario_type=draft.scenario_type.value,
        origin_city_id=origin.id,
        destination_city_id=destination.id,
        departure_date=draft.departure_date,
        return_date=draft.return_date,
        nights=(draft.return_date - draft.departure_date).days,
        adults=draft.adults,
        children_ages=list(draft.children_ages),
        transport_type=draft.transport_type.value if draft.transport_type else None,
        flight_fare_type=draft.flight_fare_type.value if draft.flight_fare_type else None,
        rail_class=draft.rail_class.value if draft.rail_class else None,
        accommodation_type=draft.accommodation_type.value if draft.accommodation_type else None,
        stars=draft.stars.value,
        meal_type=draft.meal_type.value,
        cancellation_filter=draft.cancellation_filter.value,
        calculation_profile_id=profile.id if profile else None,
        active_from=draft.active_from,
        active_until=draft.active_until or draft.return_date,
        is_active=True,
        fingerprint=fingerprint,
        priority=draft.priority,
        tags=list(draft.tags),
        notes=draft.notes,
        source_file=draft.source_file,
    )
    session.add(scenario)
    session.flush()
    logger.info("Сценарий создан", code=scenario.code, fingerprint=fingerprint[:12], actor=created_by)
    return scenario, True


def recompute_fingerprint(scenario: TravelScenario) -> str:
    """Пересчитывает отпечаток после изменения параметров сценария."""
    draft = ScenarioDraft(
        origin_city_code=scenario.origin_city.code,
        destination_city_code=scenario.destination_city.code,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        adults=scenario.adults,
        children_ages=tuple(scenario.children_ages or []),
        transport_type=TransportType(scenario.transport_type),
        flight_fare_type=FlightFareType(scenario.flight_fare_type)
        if scenario.flight_fare_type
        else None,
        rail_class=RailClass(scenario.rail_class) if scenario.rail_class else None,
        accommodation_type=AccommodationType(scenario.accommodation_type),
        stars=StarsFilter(scenario.stars),
        meal_type=MealType(scenario.meal_type),
        cancellation_filter=CancellationFilter(scenario.cancellation_filter),
    )
    scenario.fingerprint = scenario_fingerprint(build_key(draft))
    scenario.nights = (scenario.return_date - scenario.departure_date).days
    scenario.updated_at = utcnow()
    return scenario.fingerprint


# --------------------------------------------------------------------------- #
# Импорт
# --------------------------------------------------------------------------- #


def parse_children_ages(value: Any) -> tuple[int, ...]:
    """Принимает ``"7"``, ``"7;12"``, ``"7, 12"``, список или пустое значение."""
    if value in (None, "", []):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(int(item) for item in value)
    parts = re.split(r"[;,|\s]+", str(value).strip())
    return tuple(int(part) for part in parts if part)


def _parse_tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[;,|]", str(value)) if part.strip()]


def draft_from_row(row: dict[str, Any], source_file: str | None = None) -> ScenarioDraft:
    """Преобразует строку CSV/YAML в проверенный черновик сценария."""
    required = ("origin_city_code", "destination_city_code", "departure_date", "return_date")
    missing = [name for name in required if not str(row.get(name) or "").strip()]
    if missing:
        raise ValidationError(f"Не заполнены обязательные поля: {', '.join(missing)}")

    departure = parse_date(row["departure_date"])
    return_date = parse_date(row["return_date"])
    if departure is None or return_date is None:
        raise ValidationError("Некорректный формат даты (ожидается YYYY-MM-DD)")

    transport = TransportType(str(row.get("transport_type") or "AVIA").strip().upper())
    fare_raw = str(row.get("flight_fare_type") or "").strip().upper()
    rail_raw = str(row.get("rail_class") or "").strip().upper()

    flight_fare = (
        FlightFareType(fare_raw)
        if fare_raw
        else (FlightFareType.CHEAPEST if transport == TransportType.AVIA else None)
    )
    rail_class = (
        RailClass(rail_raw)
        if rail_raw
        else (RailClass.COMPARTMENT if transport == TransportType.RAIL else None)
    )
    # Тариф значим только для своего вида транспорта.
    if transport == TransportType.AVIA:
        rail_class = None
    else:
        flight_fare = None

    return ScenarioDraft(
        origin_city_code=str(row["origin_city_code"]).strip().upper(),
        destination_city_code=str(row["destination_city_code"]).strip().upper(),
        departure_date=departure,
        return_date=return_date,
        adults=int(row.get("adults") or 2),
        children_ages=parse_children_ages(row.get("children_ages")),
        transport_type=transport,
        flight_fare_type=flight_fare,
        rail_class=rail_class,
        accommodation_type=AccommodationType(
            str(row.get("accommodation_type") or "HOTEL").strip().upper()
        ),
        stars=StarsFilter(str(row.get("stars") or "ANY").strip().upper()),
        meal_type=MealType(str(row.get("meal_type") or "ANY").strip().upper()),
        cancellation_filter=CancellationFilter(
            str(row.get("cancellation_filter") or "ANY").strip().upper()
        ),
        scenario_type=ScenarioType(str(row.get("scenario_type") or "MONITORING").strip().upper()),
        code=str(row.get("code") or "").strip() or None,
        name=str(row.get("name") or "").strip() or None,
        active_from=parse_date(row.get("active_from")),
        active_until=parse_date(row.get("active_until")),
        priority=int(row.get("priority") or 100),
        tags=_parse_tags(row.get("tags")),
        notes=str(row.get("notes") or "").strip() or None,
        source_file=source_file,
    )


def parse_rows(content: str, fmt: str) -> list[dict[str, Any]]:
    """Разбирает содержимое каталога сценариев."""
    fmt = fmt.lower()
    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(content))
        return [{(k or "").strip(): v for k, v in row.items()} for row in reader]
    if fmt in ("yaml", "yml"):
        payload = yaml.safe_load(content) or {}
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        rows = payload.get("scenarios")
        if not isinstance(rows, list):
            raise ValidationError("YAML должен содержать список под ключом 'scenarios'")
        return [item for item in rows if isinstance(item, dict)]
    raise ValidationError(f"Неподдерживаемый формат импорта: {fmt}")


def import_scenarios(
    session: Session,
    content: str,
    *,
    fmt: str = "csv",
    profile: CalculationProfile | None = None,
    created_by: str | None = None,
    source_file: str | None = None,
    activate: bool = True,
) -> ImportReport:
    """Импортирует каталог сценариев. Ошибочные строки не срывают импорт."""
    report = ImportReport()
    try:
        rows = parse_rows(content, fmt)
    except ValidationError as exc:
        report.errors.append(ImportIssue(row=0, code="PARSE_ERROR", message=exc.message))
        return report

    for index, row in enumerate(rows, start=1):
        try:
            draft = draft_from_row(row, source_file)
            scenario, created = create_scenario(
                session, draft, profile=profile, created_by=created_by
            )
            if created:
                report.created += 1
                report.created_codes.append(scenario.code)
            else:
                # Существующий сценарий переактивируется, параметры не меняются:
                # отпечаток по определению совпадает.
                if activate and not scenario.is_active:
                    scenario.is_active = True
                    scenario.updated_at = utcnow()
                    report.updated += 1
                else:
                    report.skipped += 1
        except (ValidationError, NotFoundError, ConflictError) as exc:
            report.errors.append(ImportIssue(row=index, code=exc.code, message=exc.message))
        except (ValueError, KeyError) as exc:
            report.errors.append(ImportIssue(row=index, code="ROW_ERROR", message=str(exc)))

    session.flush()
    logger.info(
        "Импорт сценариев завершен",
        created=report.created,
        updated=report.updated,
        skipped=report.skipped,
        errors=len(report.errors),
    )
    return report


def export_scenarios_csv(scenarios: Iterable[TravelScenario]) -> str:
    """Выгружает сценарии в тот же CSV-формат, который принимает импорт."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for scenario in scenarios:
        writer.writerow(
            {
                "code": scenario.code,
                "name": scenario.name,
                "origin_city_code": scenario.origin_city.code,
                "destination_city_code": scenario.destination_city.code,
                "departure_date": scenario.departure_date.isoformat(),
                "return_date": scenario.return_date.isoformat(),
                "adults": scenario.adults,
                "children_ages": ";".join(str(age) for age in (scenario.children_ages or [])),
                "transport_type": scenario.transport_type,
                "flight_fare_type": scenario.flight_fare_type or "",
                "rail_class": scenario.rail_class or "",
                "accommodation_type": scenario.accommodation_type,
                "stars": scenario.stars,
                "meal_type": scenario.meal_type,
                "cancellation_filter": scenario.cancellation_filter,
                "active_from": scenario.active_from.isoformat() if scenario.active_from else "",
                "active_until": scenario.active_until.isoformat() if scenario.active_until else "",
                "priority": scenario.priority,
                "tags": ";".join(scenario.tags or []),
                "notes": scenario.notes or "",
            }
        )
    return buffer.getvalue()
