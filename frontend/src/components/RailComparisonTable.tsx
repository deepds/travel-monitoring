/**
 * Цены Туту и РЖД на одни и те же поезда.
 *
 * Строка — это поезд, а не предложение: сопоставление идет по ключу
 * эквивалентности (номер поезда, даты, класс вагона), который нормализатор
 * строит без учета источника. Поэтому сравнение честное — «поезд с поездом»,
 * а не «медиана с медианой».
 */

import { WarningOutlined } from '@ant-design/icons';
import { Alert, Empty, Segmented, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';

import { api } from '@/api/client';
import type { RailComparisonGroup } from '@/api/types';
import { AsyncBlock } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import {
  RAIL_CLASS_LABEL, dayTime, duration, money, signedPercent, sourceLabel,
} from '@/utils/format';

const { Text } = Typography;

export function RailComparisonTable({ runId }: { runId: string }) {
  const [crossOnly, setCrossOnly] = useState(true);
  const comparison = useAsync(
    () => api.runRailComparison(runId, { cross_source_only: crossOnly }),
    [runId, crossOnly],
  );

  const data = comparison.data;
  const sources = data?.sources ?? [];

  const columns = useMemo<ColumnsType<RailComparisonGroup>>(() => {
    const priceColumns: ColumnsType<RailComparisonGroup> = sources.map((code) => ({
      title: sourceLabel(code),
      key: `price-${code}`,
      align: 'right',
      width: 130,
      render: (_, row) => {
        const entry = row.prices[code];
        if (!entry || entry.total_price === null) return <Text type="secondary">нет</Text>;
        const cheapest = row.source_count > 1 && row.cheapest_source === code;
        return (
          <b style={cheapest ? { color: '#3f8600' } : undefined}>{money(entry.total_price)}</b>
        );
      },
    }));

    return [
      {
        title: 'Поезд',
        key: 'train',
        width: 150,
        render: (_, row) => (
          <Space size={4}>
            <span className="tco-monospace">
              {row.outbound_train_number ?? '—'}
              {row.inbound_train_number ? ` ↔ ${row.inbound_train_number}` : ''}
            </span>
            {row.car_type_ambiguous ? (
              <Tooltip title="Источники по-разному определили тип вагона — сравнение по этому поезду разделено.">
                <WarningOutlined style={{ color: '#faad14' }} />
              </Tooltip>
            ) : null}
            {row.sources_disagree_on_schedule ? (
              <Tooltip title="Источники показывают разное время отправления этого поезда.">
                <WarningOutlined style={{ color: '#faad14' }} />
              </Tooltip>
            ) : null}
          </Space>
        ),
      },
      {
        title: 'Вагон',
        key: 'car',
        width: 110,
        render: (_, row) =>
          row.car_type ? (
            <Tag>{RAIL_CLASS_LABEL[row.car_type] ?? row.car_type}</Tag>
          ) : (
            <Text type="secondary">{row.car_type_raw ?? '—'}</Text>
          ),
      },
      {
        title: 'Отправление',
        key: 'departure',
        width: 130,
        render: (_, row) => dayTime(row.outbound_departure_at),
      },
      {
        title: 'В пути',
        key: 'duration',
        width: 110,
        render: (_, row) => duration(row.outbound_duration_minutes),
      },
      ...priceColumns,
      {
        title: 'Разница',
        key: 'delta',
        align: 'right',
        width: 110,
        sorter: (a, b) => (a.delta_absolute ?? 0) - (b.delta_absolute ?? 0),
        render: (_, row) =>
          row.source_count > 1 && row.delta_absolute !== null ? money(row.delta_absolute) : '—',
      },
      {
        title: 'Разница, %',
        key: 'delta_ratio',
        align: 'right',
        width: 120,
        render: (_, row) =>
          row.source_count > 1 && row.delta_ratio !== null ? (
            <Text type={row.delta_ratio > 0.1 ? 'danger' : undefined}>
              {signedPercent(row.delta_ratio)}
            </Text>
          ) : (
            '—'
          ),
      },
      {
        title: 'Дешевле',
        key: 'cheapest',
        width: 110,
        render: (_, row) =>
          row.source_count > 1 && row.cheapest_source ? (
            <Tag color="green">{sourceLabel(row.cheapest_source)}</Tag>
          ) : (
            <Text type="secondary">один источник</Text>
          ),
      },
    ];
  }, [sources]);

  if (data && data.offers_available === false) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          data.reason === 'PURGED'
            ? 'Предложения этого расчета удалены по сроку хранения — сравнить источники уже нельзя.'
            : 'К расчету не привязан снимок рынка.'
        }
      />
    );
  }

  const summary = data?.summary;
  const noCrossSource = summary !== undefined && summary.cross_source_group_count === 0;

  return (
    <AsyncBlock loading={comparison.loading} error={comparison.error}>
      {noCrossSource && crossOnly ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space direction="vertical" size={4}>
              <span>Ни по одному поезду не нашлось предложений сразу от двух источников.</span>
              <Text type="secondary">
                Так бывает, когда один из источников не ответил или предложил другие поезда.
              </Text>
              <a onClick={() => setCrossOnly(false)}>Показать все поезда →</a>
            </Space>
          }
        />
      ) : (
        <>
          {summary ? (
            <Alert
              type={summary.cross_source_group_count > 0 ? 'info' : 'warning'}
              showIcon
              style={{ marginBottom: 12 }}
              message={
                summary.cross_source_group_count > 0
                  ? `Оба источника дали цену по ${summary.cross_source_group_count} поездам из ${summary.group_count}`
                  : `Поездов в выборке: ${summary.group_count}, но общих для двух источников нет`
              }
              description={
                summary.median_delta_ratio !== null ? (
                  <>
                    Типичное расхождение цен — {signedPercent(summary.median_delta_ratio)},
                    наибольшее — {signedPercent(summary.max_delta_ratio)}.
                  </>
                ) : null
              }
            />
          ) : null}

          <Space style={{ marginBottom: 12 }}>
            <Segmented
              value={crossOnly ? 'CROSS' : 'ALL'}
              onChange={(value) => setCrossOnly(value === 'CROSS')}
              options={[
                { value: 'CROSS', label: 'Только общие поезда' },
                { value: 'ALL', label: 'Все поезда' },
              ]}
            />
          </Space>

          <Table<RailComparisonGroup>
            size="small"
            rowKey={(row) => `${row.equivalence_group_id ?? row.outbound_train_number}-${row.car_type}`}
            dataSource={data?.groups ?? []}
            columns={columns}
            scroll={{ x: 1000 }}
            pagination={false}
            locale={{ emptyText: 'Поездов в этом расчете нет' }}
          />

          {data?.truncated ? (
            <Text type="secondary">Показаны первые поезда — список ограничен.</Text>
          ) : null}
        </>
      )}
    </AsyncBlock>
  );
}
