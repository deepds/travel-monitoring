"""Признаки, которые источник структурно не сообщает

Отличать «источник не отдает поле» от «не удалось классифицировать»
необходимо для допуска источника: без этого источник, чей контракт просто
не содержит, например, типа питания, снимается с расчета по доле
неклассифицированных предложений.

Revision ID: a4f1c27d9e30
Revises: 3b3c8ce72c1b
Create Date: 2026-08-04 05:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tco.db.base

revision: str = 'a4f1c27d9e30'
down_revision: str | None = '3b3c8ce72c1b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'sources',
        sa.Column(
            'unreported_attributes',
            tco.db.base.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    # Значение по умолчанию нужно только для заполнения существующих строк:
    # дальше список задается реестром источников.
    op.alter_column('sources', 'unreported_attributes', server_default=None)


def downgrade() -> None:
    op.drop_column('sources', 'unreported_attributes')
