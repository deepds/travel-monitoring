"""Инициализация справочников, профилей, источников и пользователей.

Идемпотентна: повторный запуск обновляет описательные поля и не трогает
пользовательские решения (включен ли источник, какой профиль активен).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.config import REPO_ROOT, Settings, get_settings
from tco.core.enums import (
    OfferAttribute,
    ProfileStatus,
    SourceCategory,
    SourceProtocol,
    SourceStatus,
    UserRole,
)
from tco.core.logging import get_logger
from tco.core.security import generate_password, hash_password
from tco.db.models.profile import CalculationProfile
from tco.db.models.reference import City, ScenarioTemplate, User
from tco.db.models.source import Source
from tco.core.utils import utcnow
from tco.schemas.profile import ProfileRules

logger = get_logger(__name__)

CATALOG_DIR = REPO_ROOT / "catalog"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.warning("Файл каталога не найден", path=str(path))
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# --------------------------------------------------------------------------- #
# Города
# --------------------------------------------------------------------------- #


def seed_cities(session: Session, path: Path | None = None) -> int:
    payload = _load_yaml(path or CATALOG_DIR / "cities.yaml")
    created = 0
    for entry in payload.get("cities", []):
        city = session.scalars(select(City).where(City.code == entry["code"])).first()
        if city is None:
            city = City(code=entry["code"])
            session.add(city)
            created += 1
        city.name = entry["name"]
        city.name_en = entry.get("name_en")
        city.region = entry.get("region")
        city.country_code = entry.get("country_code", "RU")
        city.timezone = entry.get("timezone", "Europe/Moscow")
        city.latitude = entry.get("latitude")
        city.longitude = entry.get("longitude")
        city.iata_codes = list(entry.get("iata_codes") or [])
        city.rail_station_codes = list(entry.get("rail_station_codes") or [])
        city.source_ids = dict(entry.get("source_ids") or {})
        city.supports_avia = bool(entry.get("supports_avia", True))
        city.supports_rail = bool(entry.get("supports_rail", True))
        city.sort_order = int(entry.get("sort_order", 100))
        city.is_active = bool(entry.get("is_active", True))
    session.flush()
    logger.info("Справочник городов синхронизирован", created=created)
    return created


# --------------------------------------------------------------------------- #
# Профили расчета
# --------------------------------------------------------------------------- #


def seed_profiles(session: Session, directory: Path | None = None) -> int:
    directory = directory or CATALOG_DIR / "profiles"
    if not directory.exists():
        logger.warning("Каталог профилей не найден", path=str(directory))
        return 0

    created = 0
    for path in sorted(directory.glob("*.yaml")):
        payload = _load_yaml(path)
        if not payload:
            continue
        code, version = payload["code"], str(payload.get("version", "1.0.0"))
        existing = session.scalars(
            select(CalculationProfile).where(
                CalculationProfile.code == code, CalculationProfile.version == version
            )
        ).first()
        # ACTIVE-версия неизменяема: существующая запись не переписывается.
        if existing is not None:
            continue

        # Валидация правил на этапе загрузки — рассинхрон схемы виден сразу.
        rules = ProfileRules.parse(payload.get("rules"))
        max_seq = session.scalar(
            select(CalculationProfile.version_seq)
            .where(CalculationProfile.code == code)
            .order_by(CalculationProfile.version_seq.desc())
            .limit(1)
        )
        profile = CalculationProfile(
            code=code,
            name=payload["name"],
            description=(payload.get("description") or "").strip() or None,
            version=version,
            version_seq=int(max_seq or 0) + 1,
            status=ProfileStatus.DRAFT.value,
            rules=rules.model_dump(mode="json"),
            created_by="bootstrap",
        )
        session.add(profile)
        created += 1

    session.flush()

    # Активируем базовый профиль, если ни один не активен.
    has_active = session.scalars(
        select(CalculationProfile).where(CalculationProfile.status == ProfileStatus.ACTIVE.value)
    ).first()
    if has_active is None:
        baseline = session.scalars(
            select(CalculationProfile)
            .where(CalculationProfile.code == "baseline")
            .order_by(CalculationProfile.version_seq.desc())
        ).first()
        if baseline is not None:
            baseline.status = ProfileStatus.ACTIVE.value
            baseline.activated_at = utcnow()
            logger.info("Базовый профиль активирован", profile=baseline.label)

    logger.info("Профили расчета синхронизированы", created=created)
    return created


# --------------------------------------------------------------------------- #
# Источники
# --------------------------------------------------------------------------- #


def seed_sources(
    session: Session, path: Path | None = None, settings: Settings | None = None
) -> int:
    from tco.connectors.registry import REGISTRY, default_config_for

    settings = settings or get_settings()
    payload = _load_yaml(path or CATALOG_DIR / "sources.yaml")
    created = 0

    for entry in payload.get("sources", []):
        code = entry["code"]
        if code not in REGISTRY:
            logger.warning("Источник из каталога не имеет коннектора", source=code)
            continue

        source = session.scalars(select(Source).where(Source.code == code)).first()
        is_new = source is None
        if source is None:
            source = Source(code=code)
            session.add(source)
            created += 1

        source.name = entry["name"]
        source.category = SourceCategory(entry["category"]).value
        source.protocol = SourceProtocol(entry.get("protocol", "REST")).value
        source.offer_types = list(entry.get("offer_types") or [])
        source.unreported_attributes = [
            OfferAttribute(item).value for item in entry.get("unreported_attributes") or []
        ]
        source.qualification_status = SourceStatus(
            entry.get("qualification_status", "CANDIDATE")
        ).value
        source.qualification_notes = (entry.get("qualification_notes") or "").strip() or None
        source.qualified_at = source.qualified_at or utcnow()
        source.requires_credentials = bool(entry.get("requires_credentials", False))
        source.is_synthetic = bool(entry.get("is_synthetic", False))
        source.allowed_hosts = list(entry.get("allowed_hosts") or [])
        source.legal_status = entry.get("legal_status")
        source.storage_allowed = bool(entry.get("storage_allowed", True))
        source.html_storage_allowed = bool(entry.get("html_storage_allowed", False))
        source.booking_horizon_days = entry.get("booking_horizon_days")
        source.rate_limit_per_minute = entry.get("rate_limit_per_minute")
        source.supported_city_codes = list(entry.get("supported_city_codes") or [])
        source.connector_version = REGISTRY[code].version
        source.config = {**default_config_for(code, settings), **(entry.get("config") or {})}

        if source.booking_horizon_days:
            today = utcnow().date()
            source.min_supported_date = today
            source.max_supported_date = today + timedelta(days=int(source.booking_horizon_days))
            source.horizon_checked_at = utcnow()

        # Включение источника — решение администратора. При первичном создании
        # оно берется из каталога и конфигурации окружения.
        if is_new:
            source.is_enabled = _initial_enabled(source, entry, settings)

    session.flush()
    logger.info("Источники синхронизированы", created=created)
    return created


def _initial_enabled(source: Source, entry: dict[str, Any], settings: Settings) -> bool:
    """Источник включается только если он реально готов к работе."""
    if source.is_synthetic:
        return bool(settings.sandbox_sources_enabled)
    if source.code == "tutu_mcp":
        return bool(entry.get("is_enabled", True)) and settings.tutu_enabled
    if source.code == "rzd":
        return bool(entry.get("is_enabled", True)) and settings.rzd_enabled
    if source.code == "yandex_travel":
        return bool(settings.yandex_travel_token)
    if source.code == "travelline":
        return bool(settings.travelline_client_id and settings.travelline_client_secret)
    return bool(entry.get("is_enabled", False))


# --------------------------------------------------------------------------- #
# Шаблоны
# --------------------------------------------------------------------------- #


def seed_templates(session: Session, path: Path | None = None) -> int:
    payload = _load_yaml(path or CATALOG_DIR / "templates.yaml")
    created = 0
    for entry in payload.get("templates", []):
        template = session.scalars(
            select(ScenarioTemplate).where(ScenarioTemplate.code == entry["code"])
        ).first()
        if template is None:
            template = ScenarioTemplate(code=entry["code"])
            session.add(template)
            created += 1
        template.name = entry["name"]
        template.description = (entry.get("description") or "").strip() or None
        template.defaults = dict(entry.get("defaults") or {})
        template.sort_order = int(entry.get("sort_order", 100))
        template.is_active = bool(entry.get("is_active", True))
    session.flush()
    logger.info("Шаблоны сценариев синхронизированы", created=created)
    return created


# --------------------------------------------------------------------------- #
# Пользователи
# --------------------------------------------------------------------------- #


def seed_users(session: Session, settings: Settings | None = None) -> dict[str, str]:
    """Создает служебных пользователей.

    Пароли берутся только из окружения. Если пароль не задан, генерируется
    случайный и однократно возвращается вызывающему коду — в БД хранится
    только хеш, в лог пароль не пишется.
    """
    settings = settings or get_settings()
    generated: dict[str, str] = {}

    definitions = [
        (settings.bootstrap_admin_username, settings.bootstrap_admin_password, UserRole.ADMIN, "Администратор"),
        (
            settings.bootstrap_analyst_username,
            settings.bootstrap_analyst_password,
            UserRole.ANALYST,
            "Аналитик",
        ),
        (
            settings.bootstrap_viewer_username,
            settings.bootstrap_viewer_password,
            UserRole.VIEWER,
            "Наблюдатель",
        ),
    ]

    for username, password, role, display_name in definitions:
        if not username:
            continue
        user = session.scalars(select(User).where(User.username == username)).first()
        if user is not None:
            continue
        secret = password or generate_password()
        if not password:
            generated[username] = secret
        session.add(
            User(
                username=username,
                display_name=display_name,
                password_hash=hash_password(secret),
                role=role.value,
                is_active=True,
            )
        )

    session.flush()
    if generated:
        logger.warning(
            "Созданы пользователи со сгенерированными паролями — задайте их через окружение",
            usernames=sorted(generated),
        )
    return generated


# --------------------------------------------------------------------------- #
# Полный bootstrap
# --------------------------------------------------------------------------- #


def bootstrap_all(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    """Полная инициализация окружения."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    stats = {
        "cities": seed_cities(session),
        "profiles": seed_profiles(session),
        "sources": seed_sources(session, settings=settings),
        "templates": seed_templates(session),
    }
    generated = seed_users(session, settings)
    stats["generated_passwords"] = generated
    return stats
