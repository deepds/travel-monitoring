"""Размах цен в расчете: min и max по компонентам и итогу

Интерфейс показывал только межквартильный диапазон P25–P75. Он устойчив, но
скрывает фактические границы рынка, а на графиках динамики полезно видеть,
в каких пределах вообще встречались предложения.

Старые расчеты остаются с ``NULL``: восстановить размах задним числом нельзя,
исходные распределения по ним не пересчитываются.

Revision ID: c9a4f8b31e27
Revises: b7d2e91a5c04
Create Date: 2026-08-04 15:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tco.db.base

revision: str = 'c9a4f8b31e27'
down_revision: str | None = 'b7d2e91a5c04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    'transport_min',
    'transport_max',
    'hotel_min',
    'hotel_max',
    'total_min',
    'total_max',
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            'scenario_runs',
            sa.Column(name, tco.db.base.Money(precision=14, scale=2), nullable=True),
        )


def downgrade() -> None:
    for name in _COLUMNS:
        op.drop_column('scenario_runs', name)
