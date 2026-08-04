"""Перечисления доменной модели.

Единственный источник истины для допустимых значений. Значения используются
в БД (как строки), в API-схемах, в UI и в экспорте, поэтому их нельзя менять
без миграции данных и повышения ``SCHEMA_VERSION``.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``str``-совместимый Enum (в 3.11 ``enum.StrEnum`` есть, но нам нужен 3.11-safe repr)."""

    def __str__(self) -> str:  # pragma: no cover - тривиально
        return str(self.value)

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]

    @classmethod
    def has(cls, value: object) -> bool:
        return any(m.value == value for m in cls)


# --------------------------------------------------------------------------- #
# Сценарии
# --------------------------------------------------------------------------- #


class ScenarioType(StrEnum):
    """Режим работы сценария (SCOPE-R S §3.3)."""

    MONITORING = "MONITORING"
    ON_DEMAND = "ON_DEMAND"
    TEMPLATE = "TEMPLATE"


class TransportType(StrEnum):
    """Авиа и ЖД — альтернативы, не комбинируются (SCOPE-R C §5)."""

    AVIA = "AVIA"
    RAIL = "RAIL"


class FlightFareType(StrEnum):
    """Тарифные режимы авиа."""

    CHEAPEST = "CHEAPEST"
    CABIN_BAGGAGE = "CABIN_BAGGAGE"
    CHECKED_BAGGAGE = "CHECKED_BAGGAGE"


class RailClass(StrEnum):
    """Классы ЖД, агрегируются раздельно."""

    RESERVED_SEAT = "RESERVED_SEAT"  # плацкарт
    COMPARTMENT = "COMPARTMENT"  # купе


class AccommodationType(StrEnum):
    HOTEL = "HOTEL"
    APARTMENT = "APARTMENT"
    GUEST_HOUSE = "GUEST_HOUSE"
    HOSTEL = "HOSTEL"
    SANATORIUM = "SANATORIUM"
    OTHER = "OTHER"  # только хранение/диагностика, в конструктор не выводится


#: Типы размещения, доступные пользователю в конструкторе MVP.
SELECTABLE_ACCOMMODATION_TYPES: tuple[AccommodationType, ...] = (
    AccommodationType.HOTEL,
    AccommodationType.APARTMENT,
    AccommodationType.GUEST_HOUSE,
    AccommodationType.HOSTEL,
    AccommodationType.SANATORIUM,
)

#: Типы размещения, для которых звездность применима.
STARRED_ACCOMMODATION_TYPES: tuple[AccommodationType, ...] = (
    AccommodationType.HOTEL,
    AccommodationType.SANATORIUM,
)


class StarsFilter(StrEnum):
    """Фильтр звездности.

    ``ANY`` — не фильтровать; ``S1``..``S5`` — конкретная категория;
    ``UNRATED`` — гостиницы без официальной классификации;
    ``NOT_APPLICABLE`` — типы размещения, где звезды неприменимы.
    """

    ANY = "ANY"
    S1 = "1"
    S2 = "2"
    S3 = "3"
    S4 = "4"
    S5 = "5"
    UNRATED = "UNRATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def numeric(self) -> int | None:
        if self in (StarsFilter.ANY, StarsFilter.UNRATED, StarsFilter.NOT_APPLICABLE):
            return None
        return int(self.value)


class MealType(StrEnum):
    """Питание. В конструктор MVP выводятся только ANY / NO_MEALS / BREAKFAST."""

    ANY = "ANY"
    NO_MEALS = "NO_MEALS"
    BREAKFAST = "BREAKFAST"
    HALF_BOARD = "HALF_BOARD"
    FULL_BOARD = "FULL_BOARD"
    ALL_INCLUSIVE = "ALL_INCLUSIVE"
    UNKNOWN = "UNKNOWN"


SELECTABLE_MEAL_TYPES: tuple[MealType, ...] = (
    MealType.ANY,
    MealType.NO_MEALS,
    MealType.BREAKFAST,
)

#: Порядок «богатства» питания: предложение удовлетворяет фильтру, если его
#: питание не хуже запрошенного.
MEAL_RANK: dict[MealType, int] = {
    MealType.NO_MEALS: 0,
    MealType.BREAKFAST: 1,
    MealType.HALF_BOARD: 2,
    MealType.FULL_BOARD: 3,
    MealType.ALL_INCLUSIVE: 4,
}


class CancellationFilter(StrEnum):
    ANY = "ANY"
    FREE_CANCELLATION = "FREE_CANCELLATION"


class CancellationType(StrEnum):
    """Классификация условия отмены конкретного предложения."""

    FREE_CANCELLATION = "FREE_CANCELLATION"
    NON_REFUNDABLE = "NON_REFUNDABLE"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------- #
# Предложения
# --------------------------------------------------------------------------- #


class OfferType(StrEnum):
    FLIGHT = "FLIGHT"
    RAIL = "RAIL"
    ACCOMMODATION = "ACCOMMODATION"


class ComponentType(StrEnum):
    """Компонент итоговой стоимости."""

    TRANSPORT = "TRANSPORT"
    ACCOMMODATION = "ACCOMMODATION"


class BaggageType(StrEnum):
    """Классификация багажа авиапредложения.

    ``UNKNOWN`` никогда не попадает в профили CABIN_BAGGAGE / CHECKED_BAGGAGE
    (SCOPE-R C §5).
    """

    UNKNOWN = "UNKNOWN"
    CABIN_ONLY = "CABIN_ONLY"
    CHECKED = "CHECKED"


class RefundabilityType(StrEnum):
    REFUNDABLE = "REFUNDABLE"
    NON_REFUNDABLE = "NON_REFUNDABLE"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"


class ValidityStatus(StrEnum):
    """Результат валидации нормализованного предложения."""

    VALID = "VALID"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_CURRENCY = "INVALID_CURRENCY"
    INVALID_ROUTE = "INVALID_ROUTE"
    INVALID_DATES = "INVALID_DATES"
    INVALID_CAPACITY = "INVALID_CAPACITY"
    INVALID_SCHEMA = "INVALID_SCHEMA"


class ClassificationStatus(StrEnum):
    """Полнота классификации предложения."""

    CLASSIFIED = "CLASSIFIED"
    UNCLASSIFIED_FARE = "UNCLASSIFIED_FARE"  # багаж/тариф не определен
    UNCLASSIFIED_MEAL = "UNCLASSIFIED_MEAL"
    UNCLASSIFIED_CANCELLATION = "UNCLASSIFIED_CANCELLATION"
    UNCLASSIFIED_CAPACITY = "UNCLASSIFIED_CAPACITY"


class OfferAttribute(StrEnum):
    """Признаки предложения, которые источник может не сообщать.

    Источник, который структурно не отдает признак, объявляет его в
    ``Source.unreported_attributes``. Это свойство контракта источника, а не
    результат конкретного сбора: отличать «источник не сообщает» от «не
    удалось классифицировать» нужно, чтобы первое не снимало источник
    с допуска по доле неклассифицированных предложений.
    """

    MEAL = "MEAL"
    CANCELLATION = "CANCELLATION"
    BAGGAGE = "BAGGAGE"
    CAPACITY = "CAPACITY"
    RAIL_CLASS = "RAIL_CLASS"


class ExclusionReason(StrEnum):
    """Почему предложение не попало в агрегированный расчет."""

    NONE = "NONE"
    INVALID = "INVALID"
    TECHNICAL_DUPLICATE = "TECHNICAL_DUPLICATE"
    PROFILE_FILTER = "PROFILE_FILTER"
    OUTLIER = "OUTLIER"
    SOURCE_NOT_ELIGIBLE = "SOURCE_NOT_ELIGIBLE"


# --------------------------------------------------------------------------- #
# Снимки, расчеты, задачи
# --------------------------------------------------------------------------- #


class SnapshotType(StrEnum):
    DAILY_MONITORING = "DAILY_MONITORING"
    ON_DEMAND = "ON_DEMAND"
    MANUAL_RETRY = "MANUAL_RETRY"
    METHODOLOGY_REPLAY = "METHODOLOGY_REPLAY"


class SnapshotStatus(StrEnum):
    COLLECTING = "COLLECTING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class RunType(StrEnum):
    MONITORING = "MONITORING"
    ON_DEMAND = "ON_DEMAND"
    REPLAY = "REPLAY"
    MANUAL = "MANUAL"


class RunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    NO_DATA = "NO_DATA"


class ComponentStatus(StrEnum):
    """Компонентные статусы, дополняющие основной статус расчета."""

    PARTIAL_TRANSPORT_MISSING = "PARTIAL_TRANSPORT_MISSING"
    PARTIAL_HOTEL_MISSING = "PARTIAL_HOTEL_MISSING"
    COMPLETE_SINGLE_SOURCE = "COMPLETE_SINGLE_SOURCE"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class SourceConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNTRUSTED = "UNTRUSTED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"
    TIMED_OUT = "TIMED_OUT"

    @property
    def is_terminal(self) -> bool:
        return self in (
            JobStatus.SUCCESS,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMED_OUT,
        )


class JobType(StrEnum):
    ON_DEMAND_CALCULATION = "ON_DEMAND_CALCULATION"
    MONITORING_SCENARIO = "MONITORING_SCENARIO"
    MONITORING_BATCH = "MONITORING_BATCH"
    SNAPSHOT_REPLAY = "SNAPSHOT_REPLAY"
    SCENARIO_IMPORT = "SCENARIO_IMPORT"
    EXPORT = "EXPORT"
    SOURCE_HEALTH_CHECK = "SOURCE_HEALTH_CHECK"
    SOURCE_CONFIDENCE = "SOURCE_CONFIDENCE"
    MAINTENANCE = "MAINTENANCE"


class ProfileStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SourceCategory(StrEnum):
    TRANSPORT = "TRANSPORT"
    ACCOMMODATION = "ACCOMMODATION"


class SourceStatus(StrEnum):
    """Результат Source Qualification (SCOPE-R E §1)."""

    APPROVED = "APPROVED"
    CONDITIONAL = "CONDITIONAL"
    REJECTED = "REJECTED"
    CANDIDATE = "CANDIDATE"


class SourceProtocol(StrEnum):
    REST = "REST"
    MCP = "MCP"
    HTML = "HTML"
    SYNTHETIC = "SYNTHETIC"


class ConnectorOutcome(StrEnum):
    """Технический итог одного обращения к источнику."""

    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    DISABLED = "DISABLED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"

    @property
    def is_ok(self) -> bool:
        return self in (ConnectorOutcome.SUCCESS, ConnectorOutcome.EMPTY)


class UserRole(StrEnum):
    VIEWER = "VIEWER"
    ANALYST = "ANALYST"
    ADMIN = "ADMIN"

    @property
    def rank(self) -> int:
        return {"VIEWER": 0, "ANALYST": 1, "ADMIN": 2}[self.value]

    def satisfies(self, required: UserRole) -> bool:
        return self.rank >= required.rank


class ExportFormat(StrEnum):
    CSV = "CSV"
    XLSX = "XLSX"


class ExportDataset(StrEnum):
    SCENARIO_RUNS = "SCENARIO_RUNS"
    OFFERS = "OFFERS"
    SOURCE_METRICS = "SOURCE_METRICS"


class AuditAction(StrEnum):
    LOGIN = "LOGIN"
    SCENARIO_CREATE = "SCENARIO_CREATE"
    SCENARIO_UPDATE = "SCENARIO_UPDATE"
    SCENARIO_ACTIVATE = "SCENARIO_ACTIVATE"
    SCENARIO_DEACTIVATE = "SCENARIO_DEACTIVATE"
    SCENARIO_DELETE = "SCENARIO_DELETE"
    SCENARIO_IMPORT = "SCENARIO_IMPORT"
    PROFILE_CREATE = "PROFILE_CREATE"
    PROFILE_ACTIVATE = "PROFILE_ACTIVATE"
    PROFILE_ARCHIVE = "PROFILE_ARCHIVE"
    PROFILE_CLONE = "PROFILE_CLONE"
    SOURCE_UPDATE = "SOURCE_UPDATE"
    SOURCE_ENABLE = "SOURCE_ENABLE"
    SOURCE_DISABLE = "SOURCE_DISABLE"
    SOURCE_HEALTH_CHECK = "SOURCE_HEALTH_CHECK"
    SOURCE_CONFIDENCE_OVERRIDE = "SOURCE_CONFIDENCE_OVERRIDE"
    MONITORING_RUN = "MONITORING_RUN"
    SNAPSHOT_RECALCULATE = "SNAPSHOT_RECALCULATE"
    JOB_RETRY = "JOB_RETRY"
    JOB_CANCEL = "JOB_CANCEL"
    EXPORT = "EXPORT"
    CACHE_PURGE = "CACHE_PURGE"
    RETENTION_CLEANUP = "RETENTION_CLEANUP"
