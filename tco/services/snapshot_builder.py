"""Построение Market Snapshot: сбор, сохранение raw, нормализация.

Снимок фиксирует состояние рынка до применения методики. После перехода
в терминальный статус он неизменяем и может обслуживать несколько расчетов
с разными профилями.

Сбор по источникам выполняется параллельно в пуле потоков: коннекторы
синхронны и проводят время в ожидании сети. Падение одного источника
не влияет на остальные.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.config import Settings, get_settings
from tco.core.enums import (
    AccommodationType,
    ConnectorOutcome,
    OfferType,
    SnapshotStatus,
    SnapshotType,
    SourceCategory,
    TransportType,
)
from tco.core.errors import StorageError
from tco.core.logging import get_logger
from tco.core.utils import floor_to_bucket, new_uuid, utcnow
from tco.connectors.base import BaseConnector
from tco.connectors.contracts import (
    AccommodationQuery,
    ConnectorResult,
    HtmlArtifact,
    RawArtifact,
    TransportQuery,
)
from tco.connectors.registry import build_context, create_connector
from tco.db.models.offer import Offer
from tco.db.models.raw import HtmlSnapshot, RawResponse
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot, SnapshotSourceResult
from tco.db.models.source import Source, SourceMetric
from tco.engine.fingerprint import snapshot_idempotency_key
from tco.normalization.normalizer import NormalizationContext, normalize
from tco.schemas.profile import ProfileRules
from tco.services.retention import expiry_for
from tco.storage.raw_store import RawStore, get_raw_store
from tco.version import NORMALIZATION_VERSION

logger = get_logger(__name__)


@dataclass(slots=True)
class CollectionTask:
    """Одно обращение: источник × тип предложений.

    Источник описан значениями, а не ORM-объектом: обращение к сети идет вне
    транзакции, а ленивое поле отсоединенного объекта в этот момент открыло бы
    новую — ровно то, от чего разделение фаз и уходит.
    """

    source_id: uuid.UUID
    source_code: str
    source_name: str
    is_synthetic: bool
    storage_allowed: bool
    html_storage_allowed: bool
    allowed_hosts: list[str]
    config: dict[str, Any]
    offer_type: OfferType
    query: TransportQuery | AccommodationQuery | None = None
    #: Заполнено у пропущенных: до какого момента разомкнут размыкатель цепи.
    circuit_open_until: datetime | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_code, self.offer_type.value)


@dataclass(slots=True)
class CollectionPlan:
    """Что предстоит собрать — без единой ссылки на сессию.

    Существует потому, что сбор нельзя вести внутри открытой транзакции.
    Прежде ``collect_into_snapshot`` вызывался внутри ``session_scope()``:
    снимок создавался до сбора, а коммит наступал после всех обращений к
    источникам. Внутри при этом работал ограничитель темпа — 4 процесса по 8
    потоков против лимита 240 запросов в минуту, — и потоки ждали в лимитере с
    открытой транзакцией. На стенде это давало ``idle in transaction`` по
    16 минут, за которыми выстраивалась очередь блокировок и вставал весь
    воркер.

    Теперь фаз три: короткая транзакция на подготовку, сбор без транзакции,
    короткая транзакция на запись. Заодно падение воркера в момент сбора
    перестает быть опасным: терять нечего, кроме самого обращения.
    """

    snapshot_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_code: str
    observed_at: datetime
    tasks: list[CollectionTask] = field(default_factory=list)
    #: Источники, которые не опрашиваются: разомкнут размыкатель цепи.
    skipped: list[CollectionTask] = field(default_factory=list)


@dataclass(slots=True)
class CollectionOutcome:
    """Итог фазы обращения к источникам."""

    results: dict[tuple[str, str], ConnectorResult] = field(default_factory=dict)
    #: Фактический момент обращения — один на снимок. От него, а не от метки
    #: часового окна, считается свежесть данных при допуске источника.
    fetched_at: datetime = field(default_factory=utcnow)
    #: Бюджет времени вышел до конца обхода.
    budget_exhausted: bool = False


@dataclass(slots=True)
class SnapshotBuildResult:
    snapshot: MarketSnapshot
    created: bool
    offers: list[Offer] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Подбор источников
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class SourceSelection:
    """Кого спросили и кого не стали.

    Разделение нужно снимку: пропущенный источник должен оставить след, иначе
    расчет не отличит «не спросили» от «спросили, ответили пусто».
    """

    pairs: list[tuple[Source, OfferType]] = field(default_factory=list)
    circuit_open: list[tuple[Source, OfferType]] = field(default_factory=list)


def eligible_sources(
    session: Session,
    *,
    offer_types: Sequence[OfferType],
    departure_date: date,
    return_date: date,
    allow_synthetic: bool,
    allowed_codes: Sequence[str] = (),
    excluded_codes: Sequence[str] = (),
) -> SourceSelection:
    """Источники, пригодные для сбора по каждому типу предложений.

    Возвращает и пропущенные по размыкателю цепи. Их обязан увидеть снимок:
    пропуск означает «мы не спросили», а не «источник ответил пусто», и без
    записи об этом расчет выходит ``NO_DATA``, неотличимым от честного
    отсутствия предложений. На витрине временная недоступность источника
    выглядела бы дырой в рынке.

    Остальные причины отсева сюда не попадают намеренно: выключенный,
    песочный или не поддерживающий тип источник — это постоянная настройка,
    и запись о нем в каждом снимке была бы шумом.
    """
    sources = session.scalars(select(Source).order_by(Source.code)).all()
    now = utcnow()
    selection = SourceSelection()

    for source in sources:
        if not source.is_usable:
            continue
        if source.is_synthetic and not allow_synthetic:
            continue
        if allowed_codes and source.code not in allowed_codes:
            continue
        if source.code in excluded_codes:
            continue
        if not (source.supports_date(departure_date) and source.supports_date(return_date)):
            continue

        # Проверка размыкателя после проверок пригодности: пропуск отмечается
        # только там, где источник иначе был бы опрошен.
        breaker_open = bool(source.circuit_open_until and source.circuit_open_until > now)
        if breaker_open:
            logger.info(
                "Источник пропущен: открыт circuit breaker",
                source=source.code,
                until=source.circuit_open_until.isoformat(),
            )
        for offer_type in offer_types:
            if not source.supports_offer_type(offer_type.value):
                continue
            if breaker_open:
                selection.circuit_open.append((source, offer_type))
            else:
                selection.pairs.append((source, offer_type))
    return selection


def build_queries(
    scenario: TravelScenario,
    source: Source,
    offer_type: OfferType,
) -> TransportQuery | AccommodationQuery:
    """Готовит нормализованный запрос к источнику."""
    origin, destination = scenario.origin_city, scenario.destination_city

    if offer_type == OfferType.ACCOMMODATION:
        return AccommodationQuery(
            city_code=destination.code,
            city_name=destination.name,
            check_in=scenario.departure_date,
            check_out=scenario.return_date,
            adults=scenario.adults,
            children_ages=tuple(scenario.children_ages or []),
            accommodation_type=scenario.accommodation_type,
            stars=scenario.stars,
            meal_type=scenario.meal_type,
            cancellation_filter=scenario.cancellation_filter,
            city_source_ids=_source_ids(destination, source.code),
            latitude=destination.latitude,
            longitude=destination.longitude,
        )

    return TransportQuery(
        origin_city_code=origin.code,
        destination_city_code=destination.code,
        origin_city_name=origin.name,
        destination_city_name=destination.name,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        adults=scenario.adults,
        children_ages=tuple(scenario.children_ages or []),
        transport_type=scenario.transport_type_enum,
        origin_source_ids=_source_ids(origin, source.code),
        destination_source_ids=_source_ids(destination, source.code),
        origin_latitude=origin.latitude,
        origin_longitude=origin.longitude,
        destination_latitude=destination.latitude,
        destination_longitude=destination.longitude,
    )


def _source_ids(city, source_code: str) -> dict[str, Any]:  # noqa: ANN001
    payload: dict[str, Any] = {}
    entry = (city.source_ids or {}).get(source_code)
    if isinstance(entry, dict):
        payload.update(entry)
    if city.iata_codes:
        payload.setdefault("iata", list(city.iata_codes))
    if city.rail_station_codes:
        payload.setdefault("station_code", list(city.rail_station_codes))
    return payload


# --------------------------------------------------------------------------- #
# Сбор
# --------------------------------------------------------------------------- #


def _make_connector(
    task: CollectionTask,
    settings: Settings,
    rules: ProfileRules,
    request_id: str,
    observation_seed: str,
    deadline: float | None,
) -> BaseConnector:
    context = build_context(
        code=task.source_code,
        name=task.source_name,
        allowed_hosts=task.allowed_hosts,
        config=dict(task.config or {}),
        request_id=request_id,
        settings=settings,
        max_offers=rules.limits.max_offers_per_source,
        soft_timeout=rules.limits.source_soft_timeout_seconds,
        hard_timeout=rules.limits.source_hard_timeout_seconds,
        max_retries=rules.limits.max_retries,
        html_storage_allowed=task.html_storage_allowed,
        observation_seed=observation_seed,
        deadline=deadline,
    )
    return create_connector(task.source_code, context)


def collect_all(
    tasks: Sequence[CollectionTask],
    *,
    settings: Settings,
    rules: ProfileRules,
    observation_seed: str,
    max_workers: int = 8,
    deadline: float | None = None,
) -> dict[tuple[str, str], ConnectorResult]:
    """Параллельно опрашивает источники. Исключения наружу не выходят.

    ``deadline`` — момент по ``time.monotonic()``, после которого коннекторы
    прекращают добирать данные и возвращают собранное. Это бюджет внутри сбора,
    а не таймаут задачи: предел времени задачи Celery снимает ее целиком вместе
    с уже полученными предложениями, а бюджет позволяет остановиться
    добровольно и сохранить их.
    """
    if not tasks:
        return {}

    def run(task: CollectionTask) -> tuple[tuple[str, str], ConnectorResult]:
        request_id = uuid.uuid4().hex
        connector = _make_connector(
            task, settings, rules, request_id, observation_seed, deadline
        )
        result = connector.safe_collect(task.offer_type, task.query)
        return task.key, result

    workers = max(1, min(max_workers, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collect") as pool:
        return dict(pool.map(run, tasks))


# --------------------------------------------------------------------------- #
# Основная сборка
# --------------------------------------------------------------------------- #


def transport_offer_type_for(scenario: TravelScenario) -> OfferType | None:
    """Тип транспортных предложений сценария; ``None`` — транспорт не наблюдается."""
    if scenario.transport_type is None:
        return None
    return (
        OfferType.FLIGHT
        if scenario.transport_type == TransportType.AVIA.value
        else OfferType.RAIL
    )


def create_snapshot(
    session: Session,
    scenario: TravelScenario,
    *,
    snapshot_type: SnapshotType = SnapshotType.ON_DEMAND,
    requested_at: datetime | None = None,
    force_refresh: bool = False,
    created_by: str | None = None,
    job_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> SnapshotBuildResult:
    """Создает «оболочку» снимка либо возвращает существующий.

    Отдельный шаг нужен для Celery-оркестрации: снимок создается один раз,
    после чего параллельные задачи наполняют его по компонентам (DELTA §4.3).
    """
    settings = settings or get_settings()
    requested_at = requested_at or utcnow()

    bucket_hours = (
        settings.snapshot_interval_hours
        if snapshot_type == SnapshotType.DAILY_MONITORING
        else 0
    )
    idempotency_key = snapshot_idempotency_key(
        fingerprint=scenario.fingerprint,
        requested_at=requested_at,
        bucket_hours=bucket_hours,
        snapshot_type=snapshot_type.value,
        # Принудительный сбор обязан дать новый снимок, а не переписать
        # существующий: без различающей соли ключ совпал бы с уже сохраненным.
        salt=requested_at.isoformat() if force_refresh else "",
    )

    if not force_refresh:
        existing = session.scalars(
            select(MarketSnapshot).where(MarketSnapshot.idempotency_key == idempotency_key)
        ).first()
        if existing is not None and existing.is_finalized:
            logger.info(
                "Снимок уже существует — повторный сбор не выполняется",
                scenario=scenario.code,
                snapshot_id=str(existing.id),
            )
            offers = session.scalars(
                select(Offer).where(Offer.market_snapshot_id == existing.id)
            ).all()
            return SnapshotBuildResult(snapshot=existing, created=False, offers=list(offers))

    observed_at = floor_to_bucket(requested_at, bucket_hours) if bucket_hours else requested_at
    observation_slot = (
        observed_at.hour // settings.snapshot_interval_hours
        if settings.snapshot_interval_hours > 0
        else None
    )

    snapshot = MarketSnapshot(
        id=new_uuid(),
        scenario_id=scenario.id,
        scenario_fingerprint=scenario.fingerprint,
        snapshot_type=snapshot_type.value,
        requested_at=requested_at,
        observed_at=observed_at,
        observation_date=observed_at.date(),
        observation_slot=observation_slot,
        status=SnapshotStatus.COLLECTING.value,
        normalization_version=NORMALIZATION_VERSION,
        scenario_params=scenario.business_params(),
        idempotency_key=idempotency_key,
        job_id=job_id,
        created_by=created_by,
        created_at=utcnow(),
    )
    session.add(snapshot)
    session.flush()
    return SnapshotBuildResult(snapshot=snapshot, created=True)


def _upsert_source_result(
    session: Session,
    snapshot: MarketSnapshot,
    source: Source,
    offer_type: OfferType,
    fields: dict[str, Any],
) -> None:
    """Пишет результат обращения к источнику, обновляя существующий.

    Запись обновляется, а не добавляется: повторный сбор в том же окне иначе
    падает на уникальном индексе, снимок остается пустой оболочкой, а расчет
    по нему выходит NO_DATA и перекрывает прежний удачный.
    """
    existing = session.scalars(
        select(SnapshotSourceResult).where(
            SnapshotSourceResult.market_snapshot_id == snapshot.id,
            SnapshotSourceResult.source_id == source.id,
            SnapshotSourceResult.offer_type == offer_type.value,
        )
    ).first()
    if existing is not None:
        for name, value in fields.items():
            setattr(existing, name, value)
        return
    session.add(
        SnapshotSourceResult(
            market_snapshot_id=snapshot.id,
            source_id=source.id,
            offer_type=offer_type.value,
            **fields,
        )
    )


def _task_from_source(
    source: Source,
    offer_type: OfferType,
    *,
    scenario: TravelScenario | None = None,
) -> CollectionTask:
    """Снимает с источника все нужные значения, пока сессия еще открыта."""
    return CollectionTask(
        source_id=source.id,
        source_code=source.code,
        source_name=source.name,
        is_synthetic=bool(source.is_synthetic),
        storage_allowed=bool(source.storage_allowed),
        html_storage_allowed=bool(source.html_storage_allowed),
        allowed_hosts=list(source.allowed_hosts or ()),
        config=dict(source.config or {}),
        offer_type=offer_type,
        query=build_queries(scenario, source, offer_type) if scenario is not None else None,
        circuit_open_until=source.circuit_open_until,
    )


def plan_collection(
    session: Session,
    snapshot: MarketSnapshot,
    scenario: TravelScenario,
    *,
    rules: ProfileRules,
    offer_types: Sequence[OfferType],
    settings: Settings | None = None,
) -> CollectionPlan:
    """Фаза 1: кого спрашивать и о чем. Короткая транзакция.

    Все, что понадобится дальше, снимается здесь: сама сеть опрашивается уже
    без открытой транзакции.
    """
    settings = settings or get_settings()
    selection = eligible_sources(
        session,
        offer_types=offer_types,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        allow_synthetic=rules.filters.allow_synthetic_sources or settings.sandbox_sources_enabled,
        allowed_codes=rules.allowed_source_codes,
        excluded_codes=rules.excluded_source_codes,
    )
    return CollectionPlan(
        snapshot_id=snapshot.id,
        scenario_id=scenario.id,
        scenario_code=scenario.code,
        observed_at=snapshot.observed_at,
        tasks=[
            _task_from_source(source, offer_type, scenario=scenario)
            for source, offer_type in selection.pairs
        ],
        skipped=[
            _task_from_source(source, offer_type)
            for source, offer_type in selection.circuit_open
        ],
    )


def execute_collection(
    plan: CollectionPlan,
    *,
    rules: ProfileRules,
    settings: Settings | None = None,
    time_budget_seconds: float | None = None,
) -> CollectionOutcome:
    """Фаза 2: обращение к источникам. Без транзакции и без сессии.

    Единственная длительная часть сбора вынесена сюда целиком. Держать на это
    время открытую транзакцию нельзя: потоки ждут в ограничителе темпа минутами,
    а база в это время не может ни очистить версии строк, ни пропустить
    ожидающих за блокировкой.
    """
    settings = settings or get_settings()
    budget = (
        settings.collection_time_budget_seconds
        if time_budget_seconds is None
        else time_budget_seconds
    )
    deadline = time.monotonic() + budget if budget and budget > 0 else None

    results = collect_all(
        plan.tasks,
        settings=settings,
        rules=rules,
        observation_seed=plan.observed_at.isoformat(),
        max_workers=settings.monitoring_batch_concurrency,
        deadline=deadline,
    )
    return CollectionOutcome(
        results=results,
        # Фактический момент обращения — один на весь снимок: сбор идет
        # параллельно и занимает секунды, а дальше следует запись, разнесенная
        # по времени сохранением raw. Отметка нужна расчету, чтобы считать
        # свежесть от сбора, а не от метки часового окна: та округлена вниз, и
        # снимок, закрытый через два часа после начала окна, объявлял себя
        # устаревшим.
        fetched_at=utcnow(),
        budget_exhausted=bool(deadline is not None and time.monotonic() >= deadline),
    )


def persist_collection(
    session: Session,
    plan: CollectionPlan,
    outcome: CollectionOutcome,
    *,
    rules: ProfileRules,
    settings: Settings | None = None,
    raw_store: RawStore | None = None,
) -> SnapshotBuildResult:
    """Фаза 3: запись собранного. Короткая транзакция.

    Снимок и сценарий перечитываются по идентификаторам: между фазами
    транзакции нет, и объекты предыдущей сюда не переносятся.
    """
    settings = settings or get_settings()
    raw_store = raw_store or get_raw_store()

    snapshot = session.get(MarketSnapshot, plan.snapshot_id)
    if snapshot is None:  # pragma: no cover — снимок создан фазой раньше
        raise ValueError(f"Снимок {plan.snapshot_id} не найден")
    scenario = session.get(TravelScenario, plan.scenario_id)
    if scenario is None:  # pragma: no cover
        raise ValueError(f"Сценарий {plan.scenario_id} не найден")

    sources = {
        source.id: source
        for source in session.scalars(
            select(Source).where(
                Source.id.in_([task.source_id for task in (*plan.tasks, *plan.skipped)])
            )
        ).all()
    }
    tasks = plan.tasks
    results = outcome.results
    fetched_at = outcome.fetched_at

    persisted_offers: list[Offer] = []
    errors: list[dict[str, Any]] = []
    connector_versions: dict[str, str] = dict(snapshot.connector_versions or {})
    raw_refs: list[str] = list(snapshot.raw_response_refs or [])
    html_refs: list[str] = list(snapshot.html_snapshot_refs or [])
    source_codes: list[str] = list(snapshot.source_codes or [])
    source_ids: list[str] = list(snapshot.source_ids or [])
    collection_summary: dict[str, Any] = dict(snapshot.collection_summary or {})
    contains_synthetic = bool(snapshot.contains_synthetic_data)

    for task in tasks:
        result = results.get(task.key)
        source = sources.get(task.source_id)
        if result is None or source is None:  # pragma: no cover - защита от рассинхрона
            continue

        connector_versions[task.source_code] = result.connector_version
        if task.source_code not in source_codes:
            source_codes.append(task.source_code)
            source_ids.append(str(task.source_id))
        if task.is_synthetic and result.offers:
            contains_synthetic = True

        raw_response = _persist_raw(
            session,
            raw_store,
            snapshot=snapshot,
            scenario=scenario,
            source=source,
            offer_type=task.offer_type,
            result=result,
            settings=settings,
            errors=errors,
        )
        if raw_response is not None:
            raw_refs.append(raw_response.storage_ref)

        html_snapshot = _persist_html(
            session,
            raw_store,
            snapshot=snapshot,
            scenario=scenario,
            source=source,
            result=result,
            raw_response=raw_response,
            settings=settings,
            errors=errors,
        )
        if html_snapshot is not None:
            html_refs.append(html_snapshot.storage_ref)

        normalized = _normalize_result(
            snapshot=snapshot,
            scenario=scenario,
            source=source,
            result=result,
            rules=rules,
            settings=settings,
            raw_response=raw_response,
            html_snapshot=html_snapshot,
        )
        for offer in normalized:
            session.add(offer)
        persisted_offers.extend(normalized)

        stats = _source_stats(result, normalized)
        collection_summary[f"{task.source_code}:{task.offer_type.value}"] = stats

        # Повторный сбор в то же окно наблюдения переиспользует снимок, а
        # результат источника уникален по тройке снимок-источник-компонента.
        # Поэтому запись обновляется, а не добавляется: иначе повторный сбор
        # падает на уникальном индексе, снимок остается пустой оболочкой, а
        # расчет по нему выходит NO_DATA и перекрывает прежний удачный.
        fields = {
            "source_code": task.source_code,
            "source_name": task.source_name,
            "is_synthetic": task.is_synthetic,
            "outcome": result.outcome.value,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
            "raw_offer_count": result.offer_count,
            "normalized_offer_count": len(normalized),
            "valid_offer_count": stats["valid_offer_count"],
            "invalid_offer_count": stats["invalid_offer_count"],
            "unclassified_offer_count": stats["unclassified_offer_count"],
            "duplicate_offer_count": 0,
            "required_field_completeness": stats["required_field_completeness"],
            "error_code": result.error_code,
            "error_message": result.error_message,
            "connector_version": result.connector_version,
            "collected_at": snapshot.observed_at,
            "fetched_at": fetched_at,
            # Выдача обрезана: обход прекращен по бюджету времени либо уперся в
            # потолок страниц. Предложения настоящие, но выборка неполна, и
            # показатель по ней описывает рынок хуже обычного.
            "is_partial": bool(result.is_partial),
        }
        _upsert_source_result(session, snapshot, source, task.offer_type, fields)
        session.add(
            SourceMetric(
                source_id=task.source_id,
                market_snapshot_id=snapshot.id,
                scenario_id=scenario.id,
                offer_type=task.offer_type.value,
                observed_at=snapshot.observed_at,
                outcome=result.outcome.value,
                http_status=result.http_status,
                latency_ms=result.latency_ms,
                attempts=result.attempts,
                raw_offer_count=result.offer_count,
                normalized_offer_count=len(normalized),
                valid_offer_count=stats["valid_offer_count"],
                invalid_offer_count=stats["invalid_offer_count"],
                unclassified_offer_count=stats["unclassified_offer_count"],
                required_field_completeness=stats["required_field_completeness"],
                error_code=result.error_code,
                error_message=result.error_message,
                connector_version=result.connector_version,
            )
        )
        _update_source_health(source, result)

        if not result.outcome.is_ok:
            errors.append(
                {
                    "source": task.source_code,
                    "offer_type": task.offer_type.value,
                    "outcome": result.outcome.value,
                    "error_code": result.error_code,
                    "message": result.error_message,
                }
            )

    # Пропущенные по размыкателю записываются наравне с опрошенными. Без этого
    # снимок выглядит так, будто источника у компоненты нет вовсе: расчет
    # выходит NO_DATA, и временная недоступность источника становится
    # неотличима от честного отсутствия предложений. На стенде так пропало
    # проживание по 395 сценариям из 465 — в логе 767 строк о пропуске, а в
    # данных ни следа.
    for task in plan.skipped:
        source = sources.get(task.source_id)
        if source is None:  # pragma: no cover
            continue
        until = task.circuit_open_until
        _upsert_source_result(
            session,
            snapshot,
            source,
            task.offer_type,
            {
                "source_code": task.source_code,
                "source_name": task.source_name,
                "is_synthetic": task.is_synthetic,
                "outcome": ConnectorOutcome.CIRCUIT_OPEN.value,
                "latency_ms": 0,
                "attempts": 0,
                "raw_offer_count": 0,
                "normalized_offer_count": 0,
                "valid_offer_count": 0,
                "invalid_offer_count": 0,
                "unclassified_offer_count": 0,
                "duplicate_offer_count": 0,
                "required_field_completeness": 0.0,
                "error_code": "CIRCUIT_OPEN",
                "error_message": (
                    "Источник не опрашивался: открыт размыкатель цепи"
                    + (f" до {until.isoformat()}" if until else "")
                ),
                "connector_version": "",
                "collected_at": snapshot.observed_at,
                "fetched_at": fetched_at,
                "is_partial": False,
            },
        )
        if task.source_code not in source_codes:
            source_codes.append(task.source_code)
            source_ids.append(str(task.source_id))
        errors.append(
            {
                "source": task.source_code,
                "offer_type": task.offer_type.value,
                "outcome": ConnectorOutcome.CIRCUIT_OPEN.value,
                "error_code": "CIRCUIT_OPEN",
                "message": "Источник не опрашивался: открыт размыкатель цепи",
            }
        )

    snapshot.source_ids = source_ids
    snapshot.source_codes = source_codes
    snapshot.raw_response_refs = raw_refs
    snapshot.html_snapshot_refs = html_refs
    snapshot.connector_versions = connector_versions
    snapshot.collection_summary = collection_summary
    snapshot.error_summary = list(snapshot.error_summary or []) + errors
    snapshot.contains_synthetic_data = contains_synthetic
    session.flush()

    logger.info(
        "Сбор компонента завершен",
        scenario=plan.scenario_code,
        snapshot_id=str(snapshot.id),
        offer_types=sorted({task.offer_type.value for task in tasks}),
        offers=len(persisted_offers),
        sources=[task.source_code for task in tasks],
        budget_exhausted=outcome.budget_exhausted,
    )
    return SnapshotBuildResult(
        snapshot=snapshot, created=False, offers=persisted_offers, errors=errors
    )


def collect_into_snapshot(
    session: Session,
    snapshot: MarketSnapshot,
    scenario: TravelScenario,
    *,
    rules: ProfileRules,
    offer_types: Sequence[OfferType],
    settings: Settings | None = None,
    raw_store: RawStore | None = None,
) -> SnapshotBuildResult:
    """Три фазы сбора в одной сессии — синхронный путь и тесты.

    Плановый сбор идет иначе: там каждая фаза получает собственную сессию, и
    сеть опрашивается вне транзакции (``tco/tasks/pipeline.py``). Здесь это
    одна транзакция намеренно: On-demand расчет укладывается в минуту и не
    держит базу заметное время, а разнесение фаз усложнило бы его без выгоды.
    """
    settings = settings or get_settings()
    plan = plan_collection(
        session,
        snapshot,
        scenario,
        rules=rules,
        offer_types=offer_types,
        settings=settings,
    )
    outcome = execute_collection(plan, rules=rules, settings=settings)
    return persist_collection(
        session, plan, outcome, rules=rules, settings=settings, raw_store=raw_store
    )


def finalize_snapshot(session: Session, snapshot: MarketSnapshot) -> MarketSnapshot:
    """Пересчитывает счетчики снимка и переводит его в терминальный статус.

    После финализации снимок неизменяем (DELTA §11.1).
    """
    if snapshot.is_finalized:
        return snapshot

    offers = session.scalars(
        select(Offer).where(Offer.market_snapshot_id == snapshot.id)
    ).all()
    results = session.scalars(
        select(SnapshotSourceResult).where(
            SnapshotSourceResult.market_snapshot_id == snapshot.id
        )
    ).all()

    transport_count = sum(
        1 for offer in offers if offer.offer_type != OfferType.ACCOMMODATION.value
    )
    snapshot.transport_offer_count = transport_count
    snapshot.accommodation_offer_count = len(offers) - transport_count
    snapshot.valid_offer_count = sum(1 for offer in offers if offer.is_valid)
    snapshot.invalid_offer_count = sum(1 for offer in offers if not offer.is_valid)
    snapshot.completed_at = utcnow()

    outcomes = [ConnectorOutcome(row.outcome) for row in results]
    if not outcomes or not any(outcome.is_ok for outcome in outcomes):
        status = SnapshotStatus.FAILED
    elif not offers:
        status = SnapshotStatus.EMPTY
    elif all(outcome.is_ok for outcome in outcomes):
        status = SnapshotStatus.COMPLETE
    else:
        status = SnapshotStatus.PARTIAL
    snapshot.status = status.value

    session.flush()
    logger.info(
        "Market Snapshot финализирован",
        snapshot_id=str(snapshot.id),
        status=snapshot.status,
        transport_offers=snapshot.transport_offer_count,
        accommodation_offers=snapshot.accommodation_offer_count,
        sources=list(snapshot.source_codes or []),
    )
    return snapshot


def build_snapshot(
    session: Session,
    scenario: TravelScenario,
    *,
    rules: ProfileRules,
    snapshot_type: SnapshotType = SnapshotType.ON_DEMAND,
    requested_at: datetime | None = None,
    force_refresh: bool = False,
    created_by: str | None = None,
    job_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    raw_store: RawStore | None = None,
) -> SnapshotBuildResult:
    """Полный синхронный цикл: оболочка → сбор обоих компонентов → финализация.

    Используется для On-demand расчета (укладывается в бюджет 60 секунд без
    накладных расходов на брокер) и в тестах. Celery-путь вызывает те же
    три шага, но параллельно через ``chord``.
    """
    settings = settings or get_settings()
    shell = create_snapshot(
        session,
        scenario,
        snapshot_type=snapshot_type,
        requested_at=requested_at,
        force_refresh=force_refresh,
        created_by=created_by,
        job_id=job_id,
        settings=settings,
    )
    if not shell.created:
        return shell

    # Собираются только наблюдаемые компоненты: к источникам за той, которую
    # сценарий не запрашивал, обращаться незачем.
    offer_types = tuple(
        item
        for item in (
            transport_offer_type_for(scenario),
            OfferType.ACCOMMODATION if scenario.observes_accommodation else None,
        )
        if item is not None
    )

    collected = collect_into_snapshot(
        session,
        shell.snapshot,
        scenario,
        rules=rules,
        offer_types=offer_types,
        settings=settings,
        raw_store=raw_store,
    )
    finalize_snapshot(session, shell.snapshot)
    return SnapshotBuildResult(
        snapshot=shell.snapshot,
        created=True,
        offers=collected.offers,
        errors=collected.errors,
    )


# --------------------------------------------------------------------------- #
# Вспомогательные шаги
# --------------------------------------------------------------------------- #


def _persist_raw(
    session: Session,
    raw_store: RawStore,
    *,
    snapshot: MarketSnapshot,
    scenario: TravelScenario,
    source: Source,
    offer_type: OfferType,
    result: ConnectorResult,
    settings: Settings,
    errors: list[dict[str, Any]],
) -> RawResponse | None:
    """Сохраняет сырые ответы. Сбой хранилища не прерывает сбор."""
    if not result.raw_artifacts or not source.storage_allowed:
        return None

    payload = [
        {
            "endpoint": artifact.endpoint,
            "request_params": artifact.request_params,
            "http_status": artifact.http_status,
            "latency_ms": artifact.latency_ms,
            "body": _artifact_body(artifact),
        }
        for artifact in result.raw_artifacts
    ]
    request_id = uuid.uuid4().hex
    try:
        stored = raw_store.put_json(
            payload,
            source_code=source.code,
            scenario_code=scenario.code,
            request_id=request_id,
            collected_at=snapshot.observed_at,
        )
    except StorageError as exc:
        logger.error("Raw storage недоступен", source=source.code, error=str(exc))
        errors.append(
            {"source": source.code, "outcome": "STORAGE_ERROR", "message": str(exc)}
        )
        return None

    first = result.raw_artifacts[0]
    raw_response = RawResponse(
        market_snapshot_id=snapshot.id,
        scenario_id=scenario.id,
        source_id=source.id,
        source_code=source.code,
        offer_type=offer_type.value,
        request_id=request_id,
        request_params=first.request_params,
        endpoint=(first.endpoint or "")[:512] or None,
        http_status=first.http_status or result.http_status,
        latency_ms=result.latency_ms,
        storage_ref=stored.ref,
        content_type=stored.content_type,
        content_encoding=stored.content_encoding,
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.checksum_sha256,
        collected_at=snapshot.observed_at,
        expires_at=expiry_for(settings.retention_raw_days, snapshot.observed_at),
        connector_version=result.connector_version,
    )
    session.add(raw_response)
    session.flush()
    return raw_response


def _persist_html(
    session: Session,
    raw_store: RawStore,
    *,
    snapshot: MarketSnapshot,
    scenario: TravelScenario,
    source: Source,
    result: ConnectorResult,
    raw_response: RawResponse | None,
    settings: Settings,
    errors: list[dict[str, Any]],
) -> HtmlSnapshot | None:
    """Сохраняет HTML-снимок, если источник браузерный и это разрешено."""
    if not result.html_artifacts:
        return None
    if not source.html_storage_allowed:
        logger.info(
            "HTML-снимок не сохранен: запрещено политикой источника", source=source.code
        )
        return None

    artifact: HtmlArtifact = result.html_artifacts[0]
    request_id = uuid.uuid4().hex
    try:
        stored = raw_store.put_html(
            artifact.html,
            source_code=source.code,
            scenario_code=scenario.code,
            request_id=request_id,
            collected_at=snapshot.observed_at,
        )
        screenshot_ref = None
        if artifact.screenshot:
            screenshot_ref = raw_store.put_bytes(
                artifact.screenshot,
                source_code=source.code,
                scenario_code=scenario.code,
                request_id=request_id,
                content_type="image/png",
                extension="png.gz",
                kind="screenshot",
                collected_at=snapshot.observed_at,
            ).ref
    except StorageError as exc:
        errors.append({"source": source.code, "outcome": "STORAGE_ERROR", "message": str(exc)})
        return None

    html_snapshot = HtmlSnapshot(
        market_snapshot_id=snapshot.id,
        raw_response_id=raw_response.id if raw_response else None,
        source_id=source.id,
        source_code=source.code,
        scenario_id=scenario.id,
        request_id=request_id,
        final_url=(artifact.final_url or "")[:2048] or None,
        http_status=artifact.http_status,
        # Заголовки проходят через redact в raw_store; здесь храним только
        # безопасное подмножество.
        response_headers=_safe_headers(artifact.response_headers),
        storage_ref=stored.ref,
        screenshot_ref=screenshot_ref,
        extraction_log=list(artifact.extraction_log or [])[:200],
        parser_version=artifact.parser_version,
        content_encoding=stored.content_encoding,
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.checksum_sha256,
        collected_at=snapshot.observed_at,
        expires_at=expiry_for(settings.retention_html_days, snapshot.observed_at),
    )
    session.add(html_snapshot)
    session.flush()
    return html_snapshot


def _normalize_result(
    *,
    snapshot: MarketSnapshot,
    scenario: TravelScenario,
    source: Source,
    result: ConnectorResult,
    rules: ProfileRules,
    settings: Settings,
    raw_response: RawResponse | None,
    html_snapshot: HtmlSnapshot | None,
) -> list[Offer]:
    context = NormalizationContext(
        scenario_id=scenario.id,
        market_snapshot_id=snapshot.id,
        source_id=source.id,
        source_code=source.code,
        connector_version=result.connector_version,
        transport_type=scenario.transport_type_enum,
        accommodation_type=scenario.accommodation_type_enum,
        departure_date=scenario.departure_date,
        return_date=scenario.return_date,
        adults=scenario.adults,
        children_ages=tuple(scenario.children_ages or []),
        base_currency=settings.base_currency,
        rules=rules,
        collected_at=snapshot.observed_at,
        raw_response_id=raw_response.id if raw_response else None,
        html_snapshot_id=html_snapshot.id if html_snapshot else None,
        raw_object_ref=raw_response.storage_ref if raw_response else None,
    )
    offers: list[Offer] = []
    for provider_offer in result.offers:
        try:
            offers.append(normalize(provider_offer, context).offer)
        except Exception as exc:  # noqa: BLE001 — одно предложение не рушит сбор
            logger.warning(
                "Не удалось нормализовать предложение",
                source=source.code,
                error=str(exc),
            )
    return offers


def _source_stats(result: ConnectorResult, offers: Sequence[Offer]) -> dict[str, Any]:
    valid = sum(1 for offer in offers if offer.is_valid)
    unclassified = sum(1 for offer in offers if offer.classification_status != "CLASSIFIED")
    completeness = None
    if offers:
        completeness = round(1.0 - unclassified / len(offers), 4)
    return {
        "outcome": result.outcome.value,
        "latency_ms": result.latency_ms,
        "raw_offer_count": result.offer_count,
        "normalized_offer_count": len(offers),
        "valid_offer_count": valid,
        "invalid_offer_count": len(offers) - valid,
        "unclassified_offer_count": unclassified,
        "required_field_completeness": completeness,
        "error_code": result.error_code,
        "diagnostics": result.diagnostics,
    }


def _update_source_health(source: Source, result: ConnectorResult) -> None:
    """Обновляет состояние circuit breaker источника."""
    settings = get_settings()
    now = utcnow()
    if result.outcome.is_ok:
        source.consecutive_failures = 0
        source.circuit_open_until = None
        source.last_success_at = now
        source.last_error = None
        return

    if result.outcome in (ConnectorOutcome.DISABLED, ConnectorOutcome.UNSUPPORTED):
        return

    source.consecutive_failures = int(source.consecutive_failures or 0) + 1
    source.last_failure_at = now
    source.last_error = (result.error_message or result.outcome.value)[:1024]
    if source.consecutive_failures >= settings.connector_circuit_breaker_failures:
        from datetime import timedelta

        source.circuit_open_until = now + timedelta(
            seconds=settings.connector_circuit_breaker_cooldown_seconds
        )
        logger.warning(
            "Circuit breaker источника разомкнут",
            source=source.code,
            failures=source.consecutive_failures,
        )


def _artifact_body(artifact: RawArtifact) -> Any:
    payload = artifact.payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")[:2_000_000]
    if isinstance(payload, str):
        return payload[:2_000_000]
    return payload


#: Исходы, после которых сценарий стоит спросить еще раз. ``EMPTY`` сюда не
#: входит намеренно: источник ответил, и «предложений нет» — это ответ о рынке,
#: а не сбой. Повторять его каждый день значило бы тратить обращения на
#: направления вроде Самара — Казань, где сообщения не существует.
RETRIABLE_OUTCOMES: frozenset[str] = frozenset(
    {
        ConnectorOutcome.TIMEOUT.value,
        ConnectorOutcome.RATE_LIMITED.value,
        ConnectorOutcome.TRANSPORT_ERROR.value,
        ConnectorOutcome.SCHEMA_ERROR.value,
        ConnectorOutcome.CIRCUIT_OPEN.value,
    }
)


def scenarios_needing_backfill(
    session: Session,
    scenarios: Sequence[TravelScenario],
    *,
    observation_date: date | None = None,
) -> list[TravelScenario]:
    """Сценарии, которые сегодня остались без наблюдения или собрались плохо.

    Два случая: снимка за день нет вовсе (задача не дошла, воркер перезапустили,
    прогон оборвался) и снимок есть, но хотя бы один источник ответил сбоем —
    таймаут, отказ транспорта, открытый размыкатель. К моменту досбора причина
    обычно уже прошла: размыкатель остывает за 900 секунд.

    Пустой ответ источника сбоем не считается, см. ``RETRIABLE_OUTCOMES``.
    """
    if not scenarios:
        return []

    observation_date = observation_date or utcnow().date()
    ids = [item.id for item in scenarios]

    observed = set(
        session.scalars(
            select(MarketSnapshot.scenario_id)
            .where(MarketSnapshot.scenario_id.in_(ids))
            .where(MarketSnapshot.observation_date == observation_date)
        ).all()
    )
    failed = set(
        session.scalars(
            select(MarketSnapshot.scenario_id)
            .join(
                SnapshotSourceResult,
                SnapshotSourceResult.market_snapshot_id == MarketSnapshot.id,
            )
            .where(MarketSnapshot.scenario_id.in_(ids))
            .where(MarketSnapshot.observation_date == observation_date)
            .where(SnapshotSourceResult.outcome.in_(RETRIABLE_OUTCOMES))
        ).all()
    )

    return [item for item in scenarios if item.id not in observed or item.id in failed]


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    """Оставляет только технические заголовки, без cookie и авторизации."""
    allowed = {
        "content-type",
        "content-length",
        "content-encoding",
        "date",
        "server",
        "cache-control",
        "etag",
        "last-modified",
    }
    return {
        key: value for key, value in (headers or {}).items() if key.lower() in allowed
    }
