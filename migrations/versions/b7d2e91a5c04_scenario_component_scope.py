"""Состав сценария: компонента может не наблюдаться

Сценарий мониторинга не обязан следить за поездкой целиком. Наблюдение только
за перелетом или только за проживанием — законный случай, поэтому
``transport_type`` и ``accommodation_type`` становятся необязательными:
``NULL`` означает, что компонента не наблюдается.

Существующие сценарии не затрагиваются — у них обе компоненты заполнены.

Revision ID: b7d2e91a5c04
Revises: a4f1c27d9e30
Create Date: 2026-08-04 14:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d2e91a5c04'
down_revision: str | None = 'a4f1c27d9e30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'travel_scenarios', 'transport_type', existing_type=sa.String(length=8), nullable=True
    )
    op.alter_column(
        'travel_scenarios', 'accommodation_type', existing_type=sa.String(length=24), nullable=True
    )


def downgrade() -> None:
    # Откат возможен только если ни один сценарий не отказался от компоненты:
    # иначе NOT NULL не на что восстановить, а придумывать значение нельзя —
    # это исказило бы смысл уже собранных по сценарию наблюдений.
    connection = op.get_bind()
    incomplete = connection.execute(
        sa.text(
            "SELECT count(*) FROM travel_scenarios "
            "WHERE transport_type IS NULL OR accommodation_type IS NULL"
        )
    ).scalar_one()
    if incomplete:
        raise RuntimeError(
            f"Откат невозможен: {incomplete} сценариев наблюдают только одну компоненту. "
            "Удалите или дополните их прежде, чем возвращать NOT NULL."
        )

    op.alter_column(
        'travel_scenarios', 'transport_type', existing_type=sa.String(length=8), nullable=False
    )
    op.alter_column(
        'travel_scenarios', 'accommodation_type', existing_type=sa.String(length=24), nullable=False
    )
