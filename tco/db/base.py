"""Базовый класс моделей и кросс-диалектные типы.

Продуктивная СУБД — PostgreSQL (JSONB, native UUID). Для быстрых юнит-тестов
поддерживается SQLite, поэтому UUID/JSON/Numeric объявлены через
``TypeDecorator`` с диалект-зависимой реализацией.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric, String, TypeDecorator
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from tco.core.utils import to_decimal, utcnow


class GUID(TypeDecorator):
    """UUID: native ``uuid`` в PostgreSQL, ``CHAR(36)`` в остальных диалектах."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONB(TypeDecorator):
    """JSONB в PostgreSQL, JSON в остальных диалектах."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB(astext_type=String()))
        return dialect.type_descriptor(JSON())


class Money(TypeDecorator):
    """Денежная сумма: ``NUMERIC(14, 2)`` c гарантированным Decimal на выходе."""

    impl = Numeric(14, 2)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return to_decimal(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return value if isinstance(value, Decimal) else to_decimal(value)


class TZDateTime(TypeDecorator):
    """``TIMESTAMP WITH TIME ZONE`` с принудительным UTC на чтении.

    SQLite теряет tzinfo, поэтому наивное значение считается UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        if isinstance(value, datetime) and value.tzinfo is not None:
            from tco.core.utils import UTC

            return value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        from tco.core.utils import UTC

        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    """Общий декларативный базовый класс."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        Decimal: Money,
        uuid.UUID: GUID,
    }

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        exclude = exclude or set()
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in exclude
        }

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
