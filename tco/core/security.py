"""Аутентификация и RBAC.

MVP использует локальную авторизацию (JWT + bcrypt). При наличии корпоративного
IAM режим ``OIDC`` позволяет валидировать внешние токены, а ``OPEN`` —
временный режим без авторизации, который обязан явно отражаться в
``/api/v1/version`` и в UI.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import bcrypt
import jwt

from tco.core.config import get_settings
from tco.core.enums import UserRole
from tco.core.errors import AuthenticationError, AuthorizationError
from tco.core.utils import utcnow

_MAX_BCRYPT_BYTES = 72


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Пароль не может быть пустым")
    payload = password.encode("utf-8")[:_MAX_BCRYPT_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_MAX_BCRYPT_BYTES], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_password(length: int = 20) -> str:
    return secrets.token_urlsafe(length)[:length]


@dataclass(frozen=True, slots=True)
class Principal:
    """Аутентифицированный субъект запроса."""

    user_id: str
    username: str
    role: UserRole
    display_name: str | None = None
    is_anonymous: bool = False

    def require(self, required: UserRole) -> None:
        if not self.role.satisfies(required):
            raise AuthorizationError(
                f"Требуется роль {required.value}, у пользователя {self.role.value}",
                details={"required_role": required.value, "actual_role": self.role.value},
            )

    def can(self, required: UserRole) -> bool:
        return self.role.satisfies(required)


ANONYMOUS_ADMIN = Principal(
    user_id="00000000-0000-0000-0000-000000000000",
    username="anonymous",
    role=UserRole.ADMIN,
    display_name="Открытый режим (без авторизации)",
    is_anonymous=True,
)

SYSTEM_PRINCIPAL = Principal(
    user_id="00000000-0000-0000-0000-000000000001",
    username="system",
    role=UserRole.ADMIN,
    display_name="Планировщик",
)


def create_access_token(
    *,
    user_id: str,
    username: str,
    role: UserRole,
    expires_minutes: int | None = None,
) -> tuple[str, int]:
    """Возвращает ``(token, expires_in_seconds)``."""
    settings = get_settings()
    ttl = expires_minutes or settings.jwt_ttl_minutes
    now = utcnow()
    expires_at = now + timedelta(minutes=ttl)
    payload: dict[str, Any] = {
        "sub": user_id,
        "username": username,
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "travel-cost-observatory",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, ttl * 60


def decode_access_token(token: str) -> Principal:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="travel-cost-observatory",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Срок действия токена истек") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Некорректный токен") from exc

    role_raw = payload.get("role", UserRole.VIEWER.value)
    if not UserRole.has(role_raw):
        raise AuthenticationError("Неизвестная роль в токене")
    return Principal(
        user_id=str(payload["sub"]),
        username=str(payload.get("username", "unknown")),
        role=UserRole(role_raw),
    )


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
