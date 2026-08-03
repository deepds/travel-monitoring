"""Общие фикстуры тестов.

Тесты выполняются на SQLite в файле временного каталога: кросс-диалектные
типы (``GUID``/``JSONB``/``Money``/``TZDateTime``) специально рассчитаны на это,
а прогон не требует поднятого PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import pytest

# Настройки должны быть заданы до первого импорта конфигурации приложения.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEPLOYMENT_MODE", "LOCAL")
# Не короче 32 байт — иначе PyJWT предупреждает о слабом ключе HMAC-SHA256.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("SANDBOX_SOURCES_ENABLED", "true")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "admin-pass-1")
os.environ.setdefault("BOOTSTRAP_ANALYST_PASSWORD", "analyst-pass-1")
os.environ.setdefault("BOOTSTRAP_VIEWER_PASSWORD", "viewer-pass-1")


@pytest.fixture(scope="session")
def _database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Отдельная файловая БД на сессию тестов."""
    path = tmp_path_factory.mktemp("db") / "test.sqlite3"
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    os.environ["DATABASE_URL"] = url

    storage = tmp_path_factory.mktemp("storage")
    os.environ["RAW_STORAGE_DIR"] = str(storage / "raw")
    os.environ["EXPORT_STORAGE_DIR"] = str(storage / "exports")

    from tco.core.config import reset_settings_cache

    reset_settings_cache()

    from tco.db.models import Base
    from tco.db.session import get_engine, reset_engine

    reset_engine()
    Base.metadata.create_all(get_engine())
    yield url
    reset_engine()


@pytest.fixture(scope="session")
def _bootstrapped(_database: str) -> Iterator[None]:
    """Справочники, профили, источники и пользователи."""
    from tco.db.session import session_scope
    from tco.services.bootstrap import bootstrap_all

    with session_scope() as session:
        bootstrap_all(session)
    yield


@pytest.fixture()
def session(_bootstrapped: None) -> Iterator:
    """Транзакционная сессия. Изменения теста откатываются."""
    from tco.db.session import get_session_factory

    db = get_session_factory()()
    try:
        yield db
        db.rollback()
    finally:
        db.close()


@pytest.fixture()
def sandbox_profile(_bootstrapped: None) -> Iterator:
    """Делает активным профиль ``sandbox``.

    Профиль ``baseline`` намеренно не допускает синтетические источники,
    поэтому сквозные тесты конвейера выполняются под ``sandbox``.
    """
    from sqlalchemy import select

    from tco.core.utils import utcnow
    from tco.db.models.profile import CalculationProfile
    from tco.db.session import session_scope

    with session_scope() as db:
        sandbox = db.scalars(
            select(CalculationProfile).where(CalculationProfile.code == "sandbox")
        ).first()
        baseline = db.scalars(
            select(CalculationProfile).where(CalculationProfile.code == "baseline")
        ).first()
        previous = (sandbox.status if sandbox else None, baseline.status if baseline else None)
        if sandbox:
            sandbox.status, sandbox.activated_at = "ACTIVE", utcnow()
        if baseline:
            baseline.status = "ARCHIVED"

    yield sandbox

    with session_scope() as db:
        sandbox = db.scalars(
            select(CalculationProfile).where(CalculationProfile.code == "sandbox")
        ).first()
        baseline = db.scalars(
            select(CalculationProfile).where(CalculationProfile.code == "baseline")
        ).first()
        if sandbox and previous[0]:
            sandbox.status = previous[0]
        if baseline and previous[1]:
            baseline.status = previous[1]


@pytest.fixture()
def client(_bootstrapped: None) -> Iterator:
    """HTTP-клиент API."""
    from fastapi.testclient import TestClient

    from tco.api.app import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _token(client, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def admin_headers(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, 'admin', 'admin-pass-1')}"}


@pytest.fixture()
def analyst_headers(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, 'analyst', 'analyst-pass-1')}"}


@pytest.fixture()
def viewer_headers(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, 'viewer', 'viewer-pass-1')}"}


@pytest.fixture()
def scenario_payload() -> dict:
    """Валидный сценарий: Москва → Сочи, авиа, гостиница 4★."""
    departure = date.today() + timedelta(days=45)
    return {
        "origin_city_code": "MOW",
        "destination_city_code": "AER",
        "departure_date": departure.isoformat(),
        "return_date": (departure + timedelta(days=5)).isoformat(),
        "adults": 2,
        "transport_type": "AVIA",
        "flight_fare_type": "CHEAPEST",
        "accommodation_type": "HOTEL",
        "stars": "4",
        "meal_type": "ANY",
        "cancellation_filter": "ANY",
        "scenario_type": "ON_DEMAND",
    }


@pytest.fixture()
def unique_scenario_payload(scenario_payload: dict) -> dict:
    """Сценарий с уникальными датами — не пересекается с другими тестами."""
    payload = dict(scenario_payload)
    offset = uuid.uuid4().int % 60
    departure = date.today() + timedelta(days=60 + offset)
    payload["departure_date"] = departure.isoformat()
    payload["return_date"] = (departure + timedelta(days=4)).isoformat()
    return payload
