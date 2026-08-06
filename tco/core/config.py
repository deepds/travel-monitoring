"""Конфигурация приложения.

Все настройки читаются из окружения (или ``.env``). Секреты в репозитории не
хранятся — см. ``.env.example``.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ #
    # Общее
    # ------------------------------------------------------------------ #
    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    timezone: str = "Europe/Moscow"

    #: Валюта расчета. Предложения в другой валюте отбраковываются
    #: (конвертация в MVP не выполняется — см. LIMITATIONS).
    base_currency: str = "RUB"

    # ------------------------------------------------------------------ #
    # Хранилища
    # ------------------------------------------------------------------ #
    database_url: str = "postgresql+psycopg://tco:tco@localhost:5432/tco"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_echo: bool = False
    #: Сколько миллисекунд СУБД терпит соединение, открывшее транзакцию и
    #: замолчавшее. Ноль отключает предохранитель.
    #:
    #: Нужен потому, что задача сбора держит сессию во время сетевых обращений:
    #: застрявший поток или оборванная управляющая сессия оставляют транзакцию
    #: открытой навсегда, и за ней встаёт очередь блокировок. Десять минут —
    #: с запасом от законной записи (секунды) и меньше предела времени задачи.
    db_idle_transaction_timeout_ms: int = 600_000

    redis_url: str | None = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    #: Выполнять celery-задачи синхронно (тесты, локальная отладка без брокера).
    celery_task_always_eager: bool = False

    #: Файловое raw-хранилище; при заданном ``s3_endpoint_url`` используется S3/MinIO.
    raw_storage_dir: Path = REPO_ROOT / "var" / "raw"
    export_storage_dir: Path = REPO_ROOT / "var" / "exports"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # ------------------------------------------------------------------ #
    # API / безопасность
    # ------------------------------------------------------------------ #
    api_prefix: str = "/api/v1"
    #: OPEN — временный режим без авторизации (должен быть явно отражен
    #: в ограничениях развертывания). LOCAL — локальный JWT. OIDC — внешний SSO.
    deployment_mode: Literal["OPEN", "LOCAL", "OIDC"] = "LOCAL"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 720
    #: ``NoDecode`` отключает разбор значения как JSON: из окружения список
    #: задается простым перечислением через запятую, а не JSON-массивом.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8501"]
    )

    #: Ограничение частоты обращений к API (запросов в минуту на пользователя/IP).
    rate_limit_per_minute: int = 240
    rate_limit_enabled: bool = True

    #: Пользователи, создаваемые при инициализации (пароли — только из env).
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = None
    bootstrap_analyst_username: str = "analyst"
    bootstrap_analyst_password: str | None = None
    bootstrap_viewer_username: str = "viewer"
    bootstrap_viewer_password: str | None = None

    # ------------------------------------------------------------------ #
    # Коннекторы
    # ------------------------------------------------------------------ #
    connector_soft_timeout_seconds: float = 20.0
    connector_hard_timeout_seconds: float = 30.0
    connector_max_retries: int = 2
    connector_backoff_base_seconds: float = 0.5
    connector_backoff_max_seconds: float = 8.0
    #: Порог последовательных ошибок для размыкания «предохранителя» источника.
    connector_circuit_breaker_failures: int = 5
    connector_circuit_breaker_cooldown_seconds: int = 900
    on_demand_job_timeout_seconds: int = 60

    tutu_mcp_url: str = "https://mcp.tutu.ru/mcp"
    tutu_enabled: bool = True

    rzd_base_url: str = "https://ticket.rzd.ru"
    rzd_enabled: bool = True

    yandex_travel_base_url: str = "https://whitelabel.travel.yandex-net.ru/hotels"
    yandex_travel_token: str | None = None

    travelline_auth_url: str = "https://partner.tlintegration.com/auth/token"
    travelline_base_url: str = "https://partner.tlintegration.com/api"
    travelline_client_id: str | None = None
    travelline_client_secret: str | None = None

    #: Синтетический источник-песочница. Позволяет прогонять весь конвейер
    #: без внешних сетевых зависимостей. В проде должен быть выключен:
    #: его предложения всегда помечаются ``is_synthetic`` и никогда не
    #: смешиваются с реальными без явного признака в explainability.
    sandbox_sources_enabled: bool = False
    sandbox_transport_enabled: bool = True
    sandbox_accommodation_enabled: bool = True

    # ------------------------------------------------------------------ #
    # Расчет и кэш
    # ------------------------------------------------------------------ #
    result_cache_ttl_minutes: int = 45
    result_cache_enabled: bool = True
    #: Интервал плановых снимков в часах. DELTA §2.1 фиксировала 6 часов
    #: (4 снимка в сутки); по решению владельца продукта наблюдение переведено
    #: на ежечасное — 24 снимка в сутки на активный сценарий. Значение задает
    #: и расписание Beat, и окно идемпотентности снимка, поэтому менять его
    #: следует только здесь.
    snapshot_interval_hours: int = 1
    monitoring_batch_concurrency: int = 8

    #: Методика для сценариев, за которыми не закреплен профиль. Задана кодом,
    #: а не «последним активированным»: активных методик может быть несколько
    #: (у витрины своя), и при выборе по времени активации появление новой
    #: молча меняло бы правила расчета всему остальному каталогу.
    default_profile_code: str = "baseline"

    #: Retention (DELTA §2.4). ``0`` — бессрочно.
    retention_raw_days: int = 45
    retention_html_days: int = 45
    retention_offers_days: int = 90
    retention_snapshot_metadata_days: int = 0
    retention_run_days: int = 0
    retention_export_days: int = 7

    #: Максимальный горизонт бронирования, показываемый пользователю (дней).
    default_booking_horizon_days: int = 180

    @field_validator("raw_storage_dir", "export_storage_dir", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url or "memory://"

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url or "cache+memory://"

    @property
    def is_open_deployment(self) -> bool:
        return self.deployment_mode == "OPEN"

    def ensure_dirs(self) -> None:
        self.raw_storage_dir.mkdir(parents=True, exist_ok=True)
        self.export_storage_dir.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Сброс кэша настроек (используется в тестах)."""
    get_settings.cache_clear()


settings = get_settings()
