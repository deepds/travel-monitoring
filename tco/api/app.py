"""Фабрика FastAPI-приложения.

Все эндпоинты живут под ``/api/v1`` с первого релиза (DELTA §5.1). Ломающие
изменения выпускаются только в новой major-версии API.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tco.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from tco.api.routers import (
    audit,
    auth,
    calculations,
    dashboard,
    exports,
    health,
    jobs,
    monitoring,
    profiles,
    reference,
    runs,
    scenarios,
    snapshots,
    sources,
    templates,
)
from tco.core.config import get_settings
from tco.core.errors import AppError, error_envelope
from tco.core.logging import configure_logging, get_logger
from tco.version import APP_NAME, APP_VERSION, METRIC_DISCLAIMER_RU

logger = get_logger(__name__)

DESCRIPTION = f"""
Платформа мониторинга стоимости путешествий (Travel Cost Observatory).

Система рассчитывает **расчетную типовую стоимость путешествия** — воспроизводимую
оценку стоимости заданного сценария на основе доступных рыночных предложений,
выбранного профиля расчета и активной версии методики.

{METRIC_DISCLAIMER_RU}

Логическая цепочка данных:
`TravelScenario → MarketSnapshot → Calculation Engine → ScenarioRun`.

Длительные операции возвращают `202 Accepted` и `job_id`; состояние доступно
через `GET /api/v1/jobs/{{job_id}}`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    configure_logging()
    settings.ensure_dirs()
    logger.info(
        "API запущено",
        environment=settings.environment,
        deployment_mode=settings.deployment_mode,
        version=APP_VERSION,
    )
    if settings.is_open_deployment:
        logger.warning(
            "Развертывание в открытом режиме — авторизация отключена. "
            "Режим допустим только как временный и должен быть отражен в ограничениях."
        )
    if settings.sandbox_sources_enabled:
        logger.warning(
            "Включен синтетический источник-песочница: результаты помечаются "
            "признаком contains_synthetic_data и не являются рыночными данными."
        )
    yield
    logger.info("API остановлено")


def create_app(settings: Any | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=DESCRIPTION,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        lifespan=lifespan,
        contact={"name": "Travel Cost Observatory"},
    )

    # Порядок важен: контекст запроса должен охватывать ограничитель частоты,
    # чтобы отказ 429 тоже нес request_id.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Correlation-Id", "Content-Disposition"],
    )

    _register_exception_handlers(app)

    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(reference.router, prefix=prefix)
    app.include_router(templates.router, prefix=prefix)
    app.include_router(scenarios.router, prefix=prefix)
    app.include_router(calculations.router, prefix=prefix)
    app.include_router(snapshots.router, prefix=prefix)
    app.include_router(runs.router, prefix=prefix)
    app.include_router(dashboard.router, prefix=prefix)
    app.include_router(sources.router, prefix=prefix)
    app.include_router(profiles.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(exports.router, prefix=prefix)
    app.include_router(audit.router, prefix=prefix)
    app.include_router(monitoring.router, prefix=prefix)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Единый error envelope для всех ошибок (DELTA §5.3)."""

    def _rid(request: Request) -> str | None:
        return getattr(request.state, "request_id", None)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.http_status >= 500:
            logger.error("Ошибка приложения", code=exc.code, message=exc.message)
        return JSONResponse(status_code=exc.http_status, content=exc.envelope(_rid(request)))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "VALIDATION_ERROR",
                "Некорректные входные данные",
                details={"fields": _compact_errors(exc.errors())},
                request_id=_rid(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(code, str(exc.detail), request_id=_rid(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Необработанное исключение", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "INTERNAL_ERROR",
                "Внутренняя ошибка сервера",
                request_id=_rid(request),
            ),
        )


def _compact_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ошибки валидации без служебного контекста pydantic."""
    compact: list[dict[str, Any]] = []
    for item in errors[:25]:
        location = [str(part) for part in item.get("loc", []) if part != "body"]
        compact.append(
            {
                "field": ".".join(location) or "body",
                "message": item.get("msg", ""),
                "type": item.get("type", ""),
            }
        )
    return compact


app = create_app()
