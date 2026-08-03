"""Health и метаданные (DELTA §6.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import select, text

from tco.api.deps import SessionDep, SettingsDep
from tco.cache.result_cache import get_result_cache
from tco.core.enums import ProfileStatus, SourceStatus
from tco.core.logging import get_logger
from tco.db.models.profile import CalculationProfile
from tco.db.models.source import Source
from tco.schemas.common import HealthComponent, HealthResponse
from tco.storage.raw_store import get_raw_store
from tco.version import APP_NAME, METRIC_DISCLAIMER_RU, METRIC_TITLE_RU, version_payload

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Полная проверка состояния")
def health(session: SessionDep, settings: SettingsDep, response: Response) -> HealthResponse:
    """Состояние всех подсистем.

    Возвращает ``503``, если недоступна база данных: без нее платформа
    неработоспособна. Недоступность кэша или raw-хранилища деградирует
    систему, но не останавливает расчеты.
    """
    components: list[HealthComponent] = []
    warnings: list[str] = []

    components.append(_database(session))
    components.append(_cache(session))
    components.append(_raw_storage())
    components.append(_profiles(session))
    components.append(_sources(session, warnings))

    if settings.is_open_deployment:
        warnings.append(
            "Развертывание в открытом режиме: авторизация отключена (временный режим)."
        )
    if settings.sandbox_sources_enabled:
        warnings.append(
            "Включен синтетический источник-песочница: часть данных не является рыночной."
        )

    critical_ok = all(c.healthy for c in components if c.name in ("database", "profiles"))
    degraded = any(not c.healthy for c in components)
    status = "ok" if not degraded else ("degraded" if critical_ok else "unhealthy")
    if not critical_ok:
        response.status_code = 503

    return HealthResponse(
        status=status,
        version=version_payload(),
        deployment_mode=settings.deployment_mode,
        components=components,
        warnings=warnings,
    )


@router.get("/health/live", summary="Проверка живости процесса")
def live() -> dict[str, str]:
    """Процесс отвечает. Не обращается к внешним подсистемам."""
    return {"status": "alive"}


@router.get("/health/ready", summary="Готовность обслуживать запросы")
def ready(session: SessionDep, response: Response) -> dict[str, Any]:
    """Готовность = доступна БД и существует активный профиль расчета."""
    database = _database(session)
    profiles = _profiles(session)
    is_ready = database.healthy and profiles.healthy
    if not is_ready:
        response.status_code = 503
    return {
        "status": "ready" if is_ready else "not_ready",
        "database": database.healthy,
        "active_profile": profiles.healthy,
    }


@router.get("/version", summary="Версии компонентов и методики")
def version(settings: SettingsDep) -> dict[str, Any]:
    """Версии, влияющие на воспроизводимость расчета.

    Каждый ``ScenarioRun`` сохраняет эти версии на момент расчета.
    """
    return {
        "app_name": APP_NAME,
        "versions": version_payload(),
        "deployment_mode": settings.deployment_mode,
        "environment": settings.environment,
        "metric_title": METRIC_TITLE_RU,
        "disclaimer": METRIC_DISCLAIMER_RU,
        "sandbox_sources_enabled": settings.sandbox_sources_enabled,
        "snapshot_interval_hours": settings.snapshot_interval_hours,
    }


# --------------------------------------------------------------------------- #
# Проверки подсистем
# --------------------------------------------------------------------------- #


def _database(session: SessionDep) -> HealthComponent:
    try:
        session.execute(text("SELECT 1"))
        return HealthComponent(name="database", healthy=True, detail="Соединение установлено")
    except Exception as exc:  # noqa: BLE001 — health не должен падать
        logger.error("База данных недоступна", error=str(exc))
        return HealthComponent(name="database", healthy=False, detail=str(exc)[:200])


def _cache(session: SessionDep) -> HealthComponent:
    try:
        stats = get_result_cache().stats(session)
        return HealthComponent(
            name="result_cache",
            healthy=True,
            detail=f"Слой: {stats.get('layer', 'database')}",
            metrics=stats,
        )
    except Exception as exc:  # noqa: BLE001
        # Кэш недоступен — расчет обязан продолжиться без него.
        return HealthComponent(name="result_cache", healthy=False, detail=str(exc)[:200])


def _raw_storage() -> HealthComponent:
    try:
        info = get_raw_store().health()
        return HealthComponent(
            name="raw_storage",
            healthy=bool(info.get("available", False)),
            detail=info.get("error") or str(info.get("backend", "")),
            metrics=info,
        )
    except Exception as exc:  # noqa: BLE001
        return HealthComponent(name="raw_storage", healthy=False, detail=str(exc)[:200])


def _profiles(session: SessionDep) -> HealthComponent:
    try:
        active = session.scalars(
            select(CalculationProfile).where(CalculationProfile.status == ProfileStatus.ACTIVE.value)
        ).all()
        return HealthComponent(
            name="profiles",
            healthy=bool(active),
            detail=(
                ", ".join(p.label for p in active) if active else "Нет активного профиля расчета"
            ),
            metrics={"active_count": len(active)},
        )
    except Exception as exc:  # noqa: BLE001
        return HealthComponent(name="profiles", healthy=False, detail=str(exc)[:200])


def _sources(session: SessionDep, warnings: list[str]) -> HealthComponent:
    """Проверяет минимальное покрытие: хотя бы один источник на компонент."""
    try:
        sources = session.scalars(select(Source)).all()
        usable = [s for s in sources if s.is_usable]
        transport = [s for s in usable if s.supports_offer_type("FLIGHT") or s.supports_offer_type("RAIL")]
        accommodation = [s for s in usable if s.supports_offer_type("ACCOMMODATION")]
        healthy = bool(transport) and bool(accommodation)

        if not transport:
            warnings.append("Нет пригодных источников транспорта — расчет невозможен.")
        if not accommodation:
            warnings.append("Нет пригодных источников проживания — расчет невозможен.")

        open_circuits = [s.code for s in usable if s.circuit_open_until is not None]
        if open_circuits:
            warnings.append(f"Разомкнут предохранитель источников: {', '.join(open_circuits)}")

        return HealthComponent(
            name="sources",
            healthy=healthy,
            detail=f"Транспорт: {len(transport)}, проживание: {len(accommodation)}",
            metrics={
                "total": len(sources),
                "usable": len(usable),
                "transport": len(transport),
                "accommodation": len(accommodation),
                "approved": sum(
                    1 for s in sources if s.qualification_status == SourceStatus.APPROVED.value
                ),
                "circuit_open": open_circuits,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return HealthComponent(name="sources", healthy=False, detail=str(exc)[:200])
