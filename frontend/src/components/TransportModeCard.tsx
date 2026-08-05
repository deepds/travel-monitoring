/**
 * Авиа против ЖД по одним и тем же направлениям.
 *
 * Сравниваются медианы транспортной составляющей, а не итоговой стоимости:
 * проживание в обоих сценариях одно и то же и только размыло бы разницу.
 * Данные берутся из /dashboard/directions, где строки уже разбиты по
 * transport_type — отдельного агрегата на бэкенде не требуется.
 */

import { Card, Empty, Table, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useMemo } from 'react';

import { api } from '@/api/client';
import type { DirectionRow } from '@/api/types';
import { AsyncBlock } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { useTheme } from '@/theme/ThemeContext';
import { applyChartTheme } from '@/utils/charts';
import { money, percent } from '@/utils/format';

const { Text } = Typography;

interface Pair {
  key: string;
  route: string;
  avia: number;
  rail: number;
  /** Насколько ЖД дешевле авиа, доля от цены авиа. */
  saving: number;
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildPairs(rows: DirectionRow[]): Pair[] {
  const byRoute = new Map<string, { name: string; avia?: number; rail?: number }>();

  for (const row of rows) {
    const value = row.median_transport;
    if (value === null || value === undefined) continue;

    const key = `${row.origin_city_code ?? ''}-${row.destination_city_code ?? ''}`;
    const entry = byRoute.get(key) ?? {
      name: `${row.origin_city_name ?? '—'} → ${row.destination_city_name ?? '—'}`,
    };
    if (row.transport_type === 'AVIA') entry.avia = value;
    if (row.transport_type === 'RAIL') entry.rail = value;
    byRoute.set(key, entry);
  }

  const pairs: Pair[] = [];
  for (const [key, entry] of byRoute) {
    // Направление без обоих способов проезда сравнивать не с чем.
    if (entry.avia === undefined || entry.rail === undefined) continue;
    pairs.push({
      key,
      route: entry.name,
      avia: entry.avia,
      rail: entry.rail,
      saving: entry.avia > 0 ? (entry.avia - entry.rail) / entry.avia : 0,
    });
  }

  return pairs.sort((a, b) => b.saving - a.saving);
}

export function TransportModeCard({ query }: { query?: Query }) {
  const { resolved } = useTheme();
  const directions = useAsync(() => api.directions(query), [JSON.stringify(query ?? {})]);
  const pairs = useMemo(() => buildPairs(directions.data?.items ?? []), [directions.data]);

  const option = useMemo(
    () => ({
      tooltip: { trigger: 'axis' },
      legend: { data: ['Авиа', 'ЖД'] },
      grid: { left: 8, right: 8, bottom: 8, top: 40, containLabel: true },
      xAxis: {
        type: 'category',
        data: pairs.map((pair) => pair.route),
        axisLabel: { interval: 0, rotate: pairs.length > 4 ? 20 : 0 },
      },
      yAxis: { type: 'value', axisLabel: { formatter: (value: number) => money(value) } },
      series: [
        { name: 'Авиа', type: 'bar', data: pairs.map((pair) => pair.avia) },
        { name: 'ЖД', type: 'bar', data: pairs.map((pair) => pair.rail) },
      ],
    }),
    [pairs],
  );

  return (
    <Card size="small" title="Авиа против ЖД" data-testid="transport-mode-card">
      <AsyncBlock loading={directions.loading} error={directions.error}>
        {pairs.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="По наблюдаемым направлениям нет пар, где собраны и авиа, и ЖД"
          />
        ) : (
          <>
            <Text type="secondary">
              Стоимость проезда без учета проживания — оно одинаково для обоих способов.
            </Text>
            <ReactECharts
              option={applyChartTheme(option, resolved)}
              style={{ height: 260, marginTop: 8 }}
              notMerge
            />

            <Table<Pair>
              size="small"
              rowKey="key"
              dataSource={pairs}
              pagination={false}
              scroll={{ x: 600 }}
              columns={[
                { title: 'Направление', dataIndex: 'route' },
                {
                  title: 'Авиа',
                  dataIndex: 'avia',
                  align: 'right',
                  render: (value: number) => money(value),
                },
                {
                  title: 'ЖД',
                  dataIndex: 'rail',
                  align: 'right',
                  render: (value: number) => money(value),
                },
                {
                  title: 'Разница',
                  dataIndex: 'saving',
                  align: 'right',
                  render: (value: number) => percent(Math.abs(value)),
                },
                {
                  title: 'Дешевле',
                  key: 'verdict',
                  width: 110,
                  render: (_, row) =>
                    Math.abs(row.saving) < 0.02 ? (
                      <Tag>сопоставимо</Tag>
                    ) : (
                      <Tag color="green">{row.saving > 0 ? 'ЖД' : 'Авиа'}</Tag>
                    ),
                },
              ]}
            />
          </>
        )}
      </AsyncBlock>
    </Card>
  );
}
