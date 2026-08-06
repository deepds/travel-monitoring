"""Индексы под внешние ключи предложений на сырые ответы

У ``offers.raw_response_id`` и ``offers.html_snapshot_id`` объявлен внешний
ключ с ``ON DELETE SET NULL``, но индекса под ним не было. PostgreSQL создает
индексы только под первичные и уникальные ключи, а проверять внешний ему
приходится при каждом удалении родительской строки — и без индекса это полный
просмотр таблицы предложений на каждый удаляемый сырой ответ.

На стенде это выглядело так: удаление 6 234 сырых ответов при 358 801
предложении не продвинулось за двадцать минут, оставаясь в состоянии
``active``. Два с лишним миллиарда операций сравнения.

Ночная очистка по сроку хранения от этого не страдает: она помечает сырые
ответы ``is_purged`` и удаляет только файлы, а строки оставляет. Страдают
операции, которые удаляют строки по-настоящему, — чистка наблюдений
(``scripts/purge_observations.py``) и удаление сценария вместе с данными
(``purge_data=true``). Обе редкие, и потому дефект дожил до объема, на котором
стал непроходимым.

Индексы создаются ``CONCURRENTLY``: таблица предложений самая большая в базе,
и обычный ``CREATE INDEX`` заблокировал бы запись на все время построения —
то есть остановил бы сбор. Отсюда же ``autocommit``: конкурентное построение
не работает внутри транзакции.

Revision ID: a7c2e5f81d63
Revises: f3a1c8d02b47
Create Date: 2026-08-07 01:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'a7c2e5f81d63'
down_revision: str | None = 'f3a1c8d02b47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: tuple[tuple[str, str], ...] = (
    ('ix_offers_raw_response', 'raw_response_id'),
    ('ix_offers_html_snapshot', 'html_snapshot_id'),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        # На SQLite конкурентного построения нет, а блокировки записи там не
        # важны: база тестовая и живет один прогон.
        for name, column in INDEXES:
            op.create_index(name, 'offers', [column])
        return

    with op.get_context().autocommit_block():
        for name, column in INDEXES:
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON offers ({column})'
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        for name, _ in INDEXES:
            op.drop_index(name, table_name='offers')
        return

    with op.get_context().autocommit_block():
        for name, _ in INDEXES:
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {name}')
