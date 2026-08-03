"""Общие схемы API: пагинация, ошибки, ответы задач."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

#: Значения по умолчанию и границы пагинации.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Единый error envelope (DELTA §5.3)."""

    error: ErrorDetail


class PageMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    has_next: bool
    has_previous: bool


class Page(BaseModel, Generic[T]):
    """Постраничный ответ."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], *, page: int, page_size: int, total: int) -> Page[T]:
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(
            items=items,
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_previous=page > 1,
            ),
        )


class JobAccepted(BaseModel):
    """Ответ на запуск длительной операции (``202 Accepted``)."""

    job_id: str
    status: str
    cached: bool = False
    status_url: str
    message: str | None = None


class OperationResult(BaseModel):
    success: bool = True
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SortParams(BaseModel):
    sort_by: str | None = None
    sort_dir: str = Field("desc", pattern="^(asc|desc)$")


class HealthComponent(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    version: dict[str, str]
    deployment_mode: str
    components: list[HealthComponent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
