"""Аутентификация (локальный режим MVP).

При наличии корпоративного Keycloak/SSO этот роутер заменяется проверкой
внешних токенов (``DEPLOYMENT_MODE=OIDC``) без изменения остальных эндпоинтов.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from tco.api.deps import PrincipalDep, SessionDep, SettingsDep
from tco.core.enums import AuditAction
from tco.core.errors import AuthenticationError
from tco.core.logging import get_logger
from tco.core.security import create_access_token, verify_password
from tco.core.utils import utcnow
from tco.db.models.reference import User
from tco.services import audit

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    username: str
    display_name: str | None = None


@router.post("/login", response_model=TokenResponse, summary="Получить токен доступа")
def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    """Проверяет пару логин/пароль и выдает JWT.

    Ответ одинаков для несуществующего пользователя и неверного пароля —
    чтобы не раскрывать существование учетных записей.
    """
    user = session.scalars(select(User).where(User.username == payload.username)).first()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        logger.warning("Неудачная попытка входа", username=payload.username[:64])
        raise AuthenticationError("Неверный логин или пароль")

    token, expires_in = create_access_token(
        user_id=str(user.id), username=user.username, role=user.role_enum
    )
    user.last_login_at = utcnow()

    audit.record(
        session,
        AuditAction.LOGIN,
        object_type="User",
        object_id=str(user.id),
        summary=f"Вход пользователя {user.username}",
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()

    logger.info("Успешный вход", username=user.username, role=user.role)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        role=user.role,
        username=user.username,
        display_name=user.display_name,
    )


@router.get("/me", summary="Текущий субъект запроса")
def me(principal: PrincipalDep, settings: SettingsDep) -> dict[str, Any]:
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role.value,
        "display_name": principal.display_name,
        "is_anonymous": principal.is_anonymous,
        "deployment_mode": settings.deployment_mode,
        "permissions": {
            "can_view": principal.can(principal.role),
            "can_calculate": principal.role.rank >= 1,
            "can_administer": principal.role.rank >= 2,
        },
    }
