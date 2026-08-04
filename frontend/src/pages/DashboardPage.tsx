import { Alert, Card, Col, Row, Segmented, Table, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '@/api/client';
import type { DirectionRow } from '@/api/types';
import {
  AsyncBlock, ChangeIndicator, LabelWithHint, MetricCard, MetricDisclaimer, PageTitle, SyntheticTag,
} from '@/components/common';
import { DEFAULT_FILTERS, DashboardFilterBar, toQuery } from '@/components/DashboardFilters';
import type { Filters } from '@/components/DashboardFilters';
import { useAsync } from '@/hooks/useAsync';
import { dateTime, money, num, percent, score, TRANSPORT_LABEL } from '@/utils/format';
import {
  AVG_QUALITY_SCORE_HINT, KPI_STABILITY_HINT, QUALITY_SCORE_SHORT_HINT,
} from '@/utils/hints';

const { Text } = Typography;

export default function DashboardPage() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [trendDays, setTrendDays] = useState(30);
  const [granularity, setGranularity] = useState<'DAY' | 'SNAPSHOT'>('DAY');
  const query = useMemo(() => toQuery(filters), [filters]);
  const deps = [JSON.stringify(query)];

  const overview = useAsync(() => api.overview(query), deps);
  const directions = useAsync(() => api.directions(query), deps);
  const structure = useAsync(() => api.costStructure(query), deps);
  const changes = useAsync(() => api.changes({ ...query, window_days: 7 }), deps);
  const trends = useAsync(
    () => api.trends({ ...query, days: trendDays, granularity }),
    [...deps, trendDays, granularity],
  );

  const data = overview.data;
  const points: any[] = trends.data?.points ?? [];

  const trendOption = useMemo(
    () => ({
      tooltip: { trigger: 'axis' },
      legend: { data: ['Итого', 'Транспорт', 'Проживание'], bottom: 0 },
      grid: { left: 60, right: 24, top: 24, bottom: 48 },
      xAxis: { type: 'category', data: points.map((p) => p.period ?? p.date ?? p.key) },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${Math.round(v / 1000)}к` } },
      series: [
        {
          name: 'Итого',
          type: 'line',
          smooth: true,
          data: points.map((p) => p.median_total),
          lineStyle: { width: 3 },
        },
        { name: 'Транспорт', type: 'line', smooth: true, data: points.map((p) => p.median_transport) },
        { name: 'Проживание', type: 'line', smooth: true, data: points.map((p) => p.median_hotel) },
      ],
    }),
    [points],
  );

  const structureOption = useMemo(() => {
    const components: any[] = structure.data?.components ?? [];
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ₽ ({d}%)' },
      legend: { bottom: 0 },
      series: [
        {
          type: 'pie',
          radius: ['45%', '70%'],
          avoidLabelOverlap: true,
          label: { formatter: '{b}\n{d}%' },
          data: components.map((c) => ({ name: c.title, value: c.median ?? 0 })),
        },
      ],
    };
  }, [structure.data]);

  const directionColumns = [
    {
      title: 'Направление',
      key: 'route',
      fixed: 'left' as const,
      render: (_: unknown, row: DirectionRow) => (
        <span>
          <b>{row.origin_city_name as string}</b> → <b>{row.destination_city_name as string}</b>{' '}
          <Tag>{TRANSPORT_LABEL[row.transport_type as string] ?? (row.transport_type as string)}</Tag>
        </span>
      ),
    },
    {
      title: 'Типовая стоимость',
      dataIndex: 'median_total_cost',
      align: 'right' as const,
      sorter: (a: DirectionRow, b: DirectionRow) =>
        (a.median_total_cost ?? 0) - (b.median_total_cost ?? 0),
      render: (value: number | null) => <b>{money(value)}</b>,
    },
    {
      title: 'P25–P75',
      key: 'range',
      align: 'right' as const,
      render: (_: unknown, row: DirectionRow) => (
        <Text type="secondary">
          {money(row.p25 as number)} — {money(row.p75 as number)}
        </Text>
      ),
    },
    {
      title: 'Транспорт',
      dataIndex: 'median_transport',
      align: 'right' as const,
      render: (value: number | null) => money(value),
    },
    {
      title: 'Проживание',
      dataIndex: 'median_hotel',
      align: 'right' as const,
      render: (value: number | null) => money(value),
    },
    {
      title: '7 дней',
      dataIndex: 'change_7d',
      align: 'right' as const,
      sorter: (a: DirectionRow, b: DirectionRow) => (a.change_7d ?? 0) - (b.change_7d ?? 0),
      render: (value: number | null) => <ChangeIndicator value={value} />,
    },
    {
      title: '30 дней',
      dataIndex: 'change_30d',
      align: 'right' as const,
      render: (value: number | null) => <ChangeIndicator value={value} />,
    },
    {
      title: <LabelWithHint text="Качество" hint={QUALITY_SCORE_SHORT_HINT} />,
      dataIndex: 'avg_quality_score',
      align: 'right' as const,
      render: (value: number | null) => score(value),
    },
    {
      title: 'Сценариев',
      key: 'count',
      align: 'right' as const,
      render: (_: unknown, row: DirectionRow) => (
        <Text type="secondary">
          {row.complete_count as number}/{row.scenario_count as number}
        </Text>
      ),
    },
    {
      title: 'Обновлено',
      dataIndex: 'last_update',
      render: (value: string | null) => <Text type="secondary">{dateTime(value)}</Text>,
    },
  ];

  return (
    <>
      <PageTitle
        title="Управленческий дашборд"
        subtitle="Расчетная типовая стоимость путешествия по наблюдаемым направлениям"
        extra={
          data?.last_update ? (
            <Text type="secondary">Данные на {dateTime(data.last_update as string)}</Text>
          ) : null
        }
      />

      <MetricDisclaimer text={data?.disclaimer as string | undefined} />

      {data?.contains_synthetic ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="В выборку попали расчеты с синтетическими данными"
          description="Они помечены отдельно и не являются наблюдением реального рынка."
        />
      ) : null}

      <DashboardFilterBar value={filters} onChange={setFilters} />

      <AsyncBlock loading={overview.loading} error={overview.error}>
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Типовая стоимость (медиана)"
              value={money(data?.median_total_cost as number)}
              hint="Медиана итоговой стоимости по всем полным расчетам выборки."
              extra={
                <span>
                  <ChangeIndicator value={data?.change_7d as number} />{' '}
                  <Text type="secondary">за 7 дней</Text>
                </span>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Изменение за 30 дней"
              value={<ChangeIndicator value={data?.change_30d as number} />}
              hint="Сравнение идет по одному и тому же множеству сценариев."
              extra={
                <Text type="secondary">
                  сопоставимых сценариев: {num(data?.comparable_scenarios_30d as number)}
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Доля транспорта"
              value={percent(data?.median_transport_share as number)}
              hint="Медианная доля транспорта в итоговой стоимости."
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Средняя оценка качества"
              value={score(data?.avg_quality_score as number)}
              hint={AVG_QUALITY_SCORE_HINT}
              extra={
                <span>
                  <Tag color="success">
                    успешных {percent(data?.success_rate as number, 0)}
                  </Tag>
                  <Tag color="warning">
                    частичных {percent(data?.partial_rate as number, 0)}
                  </Tag>
                </span>
              }
            />
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24} sm={8}>
            <MetricCard
              title="Сценариев в выборке"
              value={num(data?.scenario_count as number)}
              extra={
                <Text type="secondary">
                  полных расчетов: {num(data?.complete_count as number)} (
                  {percent(data?.complete_rate as number, 0)})
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={8}>
            <MetricCard
              title="KPI стабильности"
              value={percent(data?.kpi_pass_rate as number, 0)}
              hint={KPI_STABILITY_HINT}
            />
          </Col>
          <Col xs={24} sm={8}>
            <MetricCard
              title="Синтетические данные"
              value={data?.contains_synthetic ? 'Присутствуют' : 'Нет'}
              extra={<SyntheticTag visible={Boolean(data?.contains_synthetic)} />}
            />
          </Col>
        </Row>
      </AsyncBlock>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={16}>
          <Card
            title="Динамика стоимости"
            size="small"
            extra={
              <span style={{ display: 'flex', gap: 8 }}>
                <Segmented
                  size="small"
                  value={granularity}
                  onChange={(value) => setGranularity(value as 'DAY' | 'SNAPSHOT')}
                  options={[
                    { label: 'По дням', value: 'DAY' },
                    { label: 'По снимкам', value: 'SNAPSHOT' },
                  ]}
                />
                <Segmented
                  size="small"
                  value={trendDays}
                  onChange={(value) => setTrendDays(value as number)}
                  options={[
                    { label: '7 дн', value: 7 },
                    { label: '30 дн', value: 30 },
                    { label: '90 дн', value: 90 },
                  ]}
                />
              </span>
            }
          >
            <AsyncBlock
              loading={trends.loading}
              error={trends.error}
              empty={points.length === 0}
              emptyText="История еще не накоплена"
              minHeight={320}
            >
              <ReactECharts option={trendOption} style={{ height: 320 }} notMerge />
            </AsyncBlock>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Структура стоимости" size="small">
            <AsyncBlock
              loading={structure.loading}
              error={structure.error}
              empty={(structure.data?.components ?? []).length === 0}
              minHeight={320}
            >
              <ReactECharts option={structureOption} style={{ height: 260 }} notMerge />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {structure.data?.note as string}
              </Text>
            </AsyncBlock>
          </Card>
        </Col>
      </Row>

      <Card title="Сравнение направлений" size="small" style={{ marginBottom: 16 }}>
        <AsyncBlock
          loading={directions.loading}
          error={directions.error}
          empty={(directions.data?.items ?? []).length === 0}
          emptyText="Нет расчетов, удовлетворяющих фильтру"
        >
          <Table
            size="small"
            rowKey={(row: DirectionRow) =>
              `${row.origin_city_code}-${row.destination_city_code}-${row.transport_type}`
            }
            dataSource={directions.data?.items ?? []}
            columns={directionColumns}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            scroll={{ x: 1200 }}
          />
        </AsyncBlock>
      </Card>

      <Card title="Существенные изменения за 7 дней" size="small">
        <AsyncBlock
          loading={changes.loading}
          error={changes.error}
          empty={(changes.data?.items ?? []).length === 0}
          emptyText="Существенных изменений не выявлено"
        >
          <Table
            size="small"
            rowKey={(row: DirectionRow, index) => (row.scenario_code as string) ?? String(index)}
            dataSource={changes.data?.items ?? []}
            pagination={false}
            columns={[
              {
                title: 'Сценарий',
                dataIndex: 'scenario_code',
                render: (value: string, row: any) =>
                  row.scenario_id ? (
                    <Link to={`/scenarios/${row.scenario_id}`}>{value}</Link>
                  ) : (
                    value
                  ),
              },
              { title: 'Направление', dataIndex: 'route', render: (v: string, row: any) =>
                  v ?? `${row.origin_city_name ?? ''} → ${row.destination_city_name ?? ''}` },
              {
                title: 'Было',
                dataIndex: 'previous_total',
                align: 'right' as const,
                render: (value: number) => money(value),
              },
              {
                title: 'Стало',
                dataIndex: 'current_total',
                align: 'right' as const,
                render: (value: number) => money(value),
              },
              {
                title: 'Изменение',
                dataIndex: 'change',
                align: 'right' as const,
                render: (value: number) => <ChangeIndicator value={value} />,
              },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
