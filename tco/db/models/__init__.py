"""Все ORM-модели платформы.

Импортируются здесь, чтобы Alembic autogenerate и ``Base.metadata.create_all``
видели полный набор таблиц.
"""

from tco.db.base import Base
from tco.db.models.job import AuditEvent, ExportArtifact, Job, JobEvent
from tco.db.models.offer import AccommodationOffer, FlightOffer, Offer, RailOffer
from tco.db.models.profile import CalculationProfile
from tco.db.models.raw import HtmlSnapshot, RawResponse
from tco.db.models.reference import City, ResultCacheEntry, ScenarioTemplate, User
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot, SnapshotSourceResult
from tco.db.models.source import Source, SourceConfidence, SourceMetric

__all__ = [
    "AccommodationOffer",
    "AuditEvent",
    "Base",
    "CalculationProfile",
    "City",
    "ExportArtifact",
    "FlightOffer",
    "HtmlSnapshot",
    "Job",
    "JobEvent",
    "MarketSnapshot",
    "Offer",
    "RailOffer",
    "RawResponse",
    "ResultCacheEntry",
    "ScenarioRun",
    "ScenarioTemplate",
    "SnapshotSourceResult",
    "Source",
    "SourceConfidence",
    "SourceMetric",
    "TravelScenario",
    "User",
]
