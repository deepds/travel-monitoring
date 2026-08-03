"""Отпечатки сценария, ключ кэша и ключи идемпотентности.

Одна и та же комбинация параметров обязана давать один и тот же отпечаток
в любом процессе и при любом порядке полей — за это отвечает канонический JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    FlightFareType,
    MealType,
    RailClass,
    RunType,
    StarsFilter,
    TransportType,
)
from tco.core.utils import floor_to_bucket, stable_hash, utcnow


@dataclass(frozen=True, slots=True)
class ScenarioKey:
    """Бизнес-параметры сценария, определяющие его тождественность."""

    origin_city_code: str
    destination_city_code: str
    departure_date: date
    return_date: date
    adults: int
    children_ages: tuple[int, ...]
    transport_type: TransportType
    flight_fare_type: FlightFareType | None
    rail_class: RailClass | None
    accommodation_type: AccommodationType
    stars: StarsFilter
    meal_type: MealType
    cancellation_filter: CancellationFilter

    def as_dict(self) -> dict:
        return {
            "origin": self.origin_city_code,
            "destination": self.destination_city_code,
            "departure_date": self.departure_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "adults": int(self.adults),
            "children_ages": sorted(int(age) for age in self.children_ages),
            "transport_type": str(self.transport_type),
            # Тариф значим только для соответствующего вида транспорта: сценарий
            # AVIA с заполненным rail_class тождественен тому же без него.
            "flight_fare_type": (
                str(self.flight_fare_type)
                if self.transport_type == TransportType.AVIA and self.flight_fare_type
                else None
            ),
            "rail_class": (
                str(self.rail_class)
                if self.transport_type == TransportType.RAIL and self.rail_class
                else None
            ),
            "accommodation_type": str(self.accommodation_type),
            "stars": str(self.stars),
            "meal_type": str(self.meal_type),
            "cancellation_filter": str(self.cancellation_filter),
        }


def scenario_fingerprint(key: ScenarioKey) -> str:
    """Стабильный отпечаток сценария (64 hex-символа)."""
    return stable_hash(key.as_dict())


def cache_key(key: ScenarioKey, profile_version: str, profile_code: str = "") -> str:
    """Ключ Result Cache: параметры сценария + версия профиля (SCOPE-R P §13)."""
    return stable_hash(
        {
            "scenario": key.as_dict(),
            "profile_code": profile_code,
            "profile_version": profile_version,
        }
    )


def snapshot_idempotency_key(
    *,
    fingerprint: str,
    requested_at: datetime | None,
    bucket_hours: int,
    snapshot_type: str,
) -> str:
    """Ключ идемпотентности снимка.

    Повторный запуск в пределах одного временного окна не создает дублирующий
    ``MarketSnapshot`` (DELTA §4.5), если не запрошен ``force_refresh``.
    """
    moment = requested_at or utcnow()
    bucket = floor_to_bucket(moment, bucket_hours) if bucket_hours > 0 else moment
    return stable_hash(
        {
            "fingerprint": fingerprint,
            "bucket": bucket.isoformat(),
            "snapshot_type": snapshot_type,
        }
    )


def job_idempotency_key(
    *,
    fingerprint: str,
    requested_at: datetime | None,
    bucket_hours: int,
    profile_version: str,
    run_type: RunType | str,
    salt: str = "",
) -> str:
    """Ключ идемпотентности задачи расчета (DELTA §4.5)."""
    moment = requested_at or utcnow()
    bucket = floor_to_bucket(moment, bucket_hours) if bucket_hours > 0 else moment
    return stable_hash(
        {
            "fingerprint": fingerprint,
            "bucket": bucket.isoformat(),
            "profile_version": profile_version,
            "run_type": str(run_type),
            "salt": salt,
        }
    )
