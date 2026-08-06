"""Фактическое время обращения к источнику отдельно от метки окна

Свежесть данных проверялась по ``collected_at`` результата источника, а это
метка окна наблюдения, округленная вниз до часа. Снимок, помеченный 09:00 и
закрытый в 11:05, объявлялся устаревшим на 125 минут при пороге 120 — хотя
предложения в нем собраны минуту назад. Источник в таком снимке не допускался
к расчету, и расчет выходил ``NO_DATA`` при полных данных.

Проявлялось это не изредка: суточный прогон сетки идет по лимиту темпа
источников дольше двух часов всегда, поэтому выпадало все, что собрано во
второй половине прогона. Замер на стенде — 799 расчетов из 1240 за один прогон,
при том что РЖД отдал по ним 64 валидных предложения с медианой 14 122 рубля, а
Туту 137 с медианой 18 666.

``collected_at`` не трогаем: округление сделано ради идемпотентности, и на него
опираются журналы и уже выгруженные слепки.

Существующим записям время проставляется из ``completed_at`` снимка — это
ближайшая известная правда: сбор закончился не позже закрытия снимка. Без этого
переноса историю нельзя было бы пересчитать исправленной методикой: старые
записи снова дали бы ``NO_DATA``, ради чего снимок и сделан неизменяемым.

Revision ID: e2f7b04c9a15
Revises: d1e5a63f4b82
Create Date: 2026-08-06 18:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e2f7b04c9a15'
down_revision: str | None = 'd1e5a63f4b82'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'snapshot_source_results',
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Перенос: время закрытия снимка как оценка сверху для фактического сбора.
    # Записи снимков, оставшихся незакрытыми, остаются с NULL — у них
    # фактического времени нет, и свежесть у них считается по-старому.
    op.execute(
        sa.text(
            """
            UPDATE snapshot_source_results AS r
            SET fetched_at = m.completed_at
            FROM market_snapshots AS m
            WHERE m.id = r.market_snapshot_id AND m.completed_at IS NOT NULL
            """
        )
        if op.get_bind().dialect.name == 'postgresql'
        else sa.text(
            """
            UPDATE snapshot_source_results
            SET fetched_at = (
                SELECT m.completed_at FROM market_snapshots AS m
                WHERE m.id = snapshot_source_results.market_snapshot_id
            )
            WHERE EXISTS (
                SELECT 1 FROM market_snapshots AS m
                WHERE m.id = snapshot_source_results.market_snapshot_id
                  AND m.completed_at IS NOT NULL
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column('snapshot_source_results', 'fetched_at')
