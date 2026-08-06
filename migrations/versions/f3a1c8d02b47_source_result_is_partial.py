"""Пометка обрезанной выдачи источника

Поиск отдает не больше тридцати объектов за раз и не сортирует их: какие
именно тридцать попадут в ответ, решает источник. Пока читалась одна страница,
95 % запросов по отелям и 90 % по авиа упирались в потолок, и медиана считалась
по случайной трети рынка — по Казани 7 984 рубля вместо 6 923 по всем
84 объектам.

Теперь выдача дочитывается постранично, но обход может прекратиться раньше
конца: по бюджету времени сбора или по потолку страниц. Такое наблюдение
отличается и от полного, и от ошибки — обращение состоялось, предложения
настоящие, но выборка неполна. Без отдельной пометки оно неотличимо от полного
на экране «Покрытие и качество».

Существующим записям проставляется ``false``: они собраны до пагинации, и хотя
почти все обрезаны, отличить обрезанные от полных в них уже нельзя — обход
тогда не велся вовсе. Помечать их частичными задним числом значило бы выдать
догадку за наблюдение.

Revision ID: f3a1c8d02b47
Revises: e2f7b04c9a15
Create Date: 2026-08-06 21:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f3a1c8d02b47'
down_revision: str | None = 'e2f7b04c9a15'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'snapshot_source_results',
        sa.Column('is_partial', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('snapshot_source_results', 'is_partial')
