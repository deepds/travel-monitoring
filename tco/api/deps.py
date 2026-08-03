"""Зависимости FastAPI: сессия БД, аутентификация, RBAC, пагинация.

Роли проверяются только на backend (DELTA §5.2): UI не может расширить права
пользователя, скрыв или показав элемент управления.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.config import Settings, get_settings
from tco.core.enums import UserRole
from tco.core.errors import AuthenticationError, NotFoundError
from tco.core.security import ANONYMOUS_ADMIN, Principal, decode_access_token
from tco.db.models.reference import User
from tco.db.session import db_session
from tco.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

# --------------------------------------------------------------------------- #
# Инфраструктура
# --------------------------------------------------------------------------- #

SessionDep = Annotated[Session, Depends(db_session)]


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


def request_id(request: Request) -> str:
    """Идентификатор запроса, проставленный middleware."""
    return getattr(request.state, "request_id", "")


RequestIdDep = Annotated[str, Depends(request_id)]


# --------------------------------------------------------------------------- #
# Аутентификация
# --------------------------------------------------------------------------- #


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def current_principal(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Определяет субъекта запроса.

    В режиме ``OPEN`` авторизация отключена — это временный режим развертывания,
    который обязан быть отражен в ``GET /api/v1/version`` и в UI.
    """
    if settings.is_open_deployment:
        request.state.principal = ANONYMOUS_ADMIN
        return ANONYMOUS_ADMIN

    token = _bearer_token(authorization)
    if not token:
        raise AuthenticationError("Не передан токен доступа")

    principal = decode_access_token(token)

    # Токен может пережить блокировку пользователя — проверяем актуальность.
    user = session.get(User, uuid.UUID(principal.user_id))
    if user is None or not user.is_active:
        raise AuthenticationError("Пользователь заблокирован или удален")
    principal = Principal(
        user_id=str(user.id),
        username=user.username,
        role=user.role_enum,
        display_name=user.display_name,
    )
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require_role(required: UserRole):
    """Фабрика зависимости, требующей минимальную роль."""

    def _dependency(principal: PrincipalDep) -> Principal:
        principal.require(required)
        return principal

    return _dependency


ViewerDep = Annotated[Principal, Depends(require_role(UserRole.VIEWER))]
AnalystDep = Annotated[Principal, Depends(require_role(UserRole.ANALYST))]
AdminDep = Annotated[Principal, Depends(require_role(UserRole.ADMIN))]


# --------------------------------------------------------------------------- #
# Пагинация и сортировка
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination(
    page: Annotated[int, Query(ge=1, description="Номер страницы")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Размер страницы")
    ] = DEFAULT_PAGE_SIZE,
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


PaginationDep = Annotated[Pagination, Depends(pagination)]


def apply_sort(stmt: Any, model: Any, sort_by: str | None, sort_dir: str, allowed: dict[str, Any]):
    """Применяет сортировку по allowlist полей.

    Произвольное имя поля не попадает в SQL — только заранее разрешенное.
    """
    column = allowed.get(sort_by or "")
    if column is None:
        return stmt
    return stmt.order_by(column.desc() if sort_dir == "desc" else column.asc())


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #


def parse_uuid(value: str, *, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise NotFoundError(f"Некорректный идентификатор {field}: {value}") from exc


def get_or_404(session: Session, model: type, entity_id: str | uuid.UUID, name: str) -> Any:
    entity = session.get(model, parse_uuid(str(entity_id)))
    if entity is None:
        raise NotFoundError(f"{name} {entity_id} не найден")
    return entity


def by_code_or_404(session: Session, model: type, code: str, name: str) -> Any:
    entity = session.scalars(select(model).where(model.code == code)).first()
    if entity is None:
        raise NotFoundError(f"{name} {code} не найден")
    return entity


def idempotency_key_header(
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Ключ идемпотентности создающей операции"),
    ] = None,
) -> str | None:
    return idempotency_key


IdempotencyKeyDep = Annotated[str | None, Depends(idempotency_key_header)]


def session_iterator() -> Iterator[Session]:  # pragma: no cover - псевдоним для читаемости
    yield from db_session()
