"""Принадлежность сценария к сетке витрины — поле, а не тег

Сетка наблюдений витрины отличалась от каталога тегом в JSONB. Отобрать по
нему в SQL нельзя переносимо: ``JSONB.contains`` есть только в PostgreSQL, а на
SQLite молча не находит ничего — фильтр по тегу прошел бы тесты, ничего не
отфильтровав, и разошелся бы с боевой базой в тишине.

Из-за этого агрегаты дашборда считались по сетке наравне с каталогом. На стенде
это дало провал медианы со 104 528 до 39 тысяч в тот день, когда сетка
наполнилась: 3011 односоставных расчетов по 21 550 рублей влились в выборку из
2333 каталожных по 91 019. На графике это выглядело обвалом рынка на 60
процентов и восстановлением назавтра.

Признак становится полем: он описывает сценарий, а не помечает его. В отпечаток
не входит — принадлежность к сетке не меняет наблюдаемую поездку, и смена
признака не должна разрывать историю наблюдений.

Revision ID: d1e5a63f4b82
Revises: c9a4f8b31e27
Create Date: 2026-08-06 04:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd1e5a63f4b82'
down_revision: str | None = 'c9a4f8b31e27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Тег, которым сетка помечалась раньше. Значение перенесено сюда намеренно:
#: миграция должна пережить переименование или исчезновение константы в коде.
GRID_TAG = 'showcase-grid'


def upgrade() -> None:
    op.add_column(
        'travel_scenarios',
        sa.Column(
            'is_showcase_grid',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        'ix_scenarios_showcase_grid', 'travel_scenarios', ['is_showcase_grid']
    )

    # Перенос существующей разметки. Тег остается на месте: по нему сетку
    # отличают журналы и уже выгруженные слепки, и потеря разметки в них
    # сделала бы старые выгрузки нечитаемыми.
    connection = op.get_bind()
    if connection.dialect.name == 'postgresql':
        condition = "tags @> :tag"
        params = {'tag': f'["{GRID_TAG}"]'}
    else:
        # SQLite хранит JSON текстом; для разовой миграции подстроки хватает,
        # а полноценного разбора массива у диалекта нет.
        condition = "CAST(tags AS TEXT) LIKE :tag"
        params = {'tag': f'%"{GRID_TAG}"%'}

    connection.execute(
        sa.text(f"UPDATE travel_scenarios SET is_showcase_grid = true WHERE {condition}"),
        params,
    )


def downgrade() -> None:
    op.drop_index('ix_scenarios_showcase_grid', table_name='travel_scenarios')
    op.drop_column('travel_scenarios', 'is_showcase_grid')
