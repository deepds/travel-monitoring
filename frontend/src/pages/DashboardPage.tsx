import { Card, Col, Row, Segmented, Table, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '@/api/client';
import type { DirectionRow } from '@/api/types';
import {
  AsyncBlock, ChangeIndicator, LabelWithHint, MetricCard, MetricDisclaimer, PageTitle, PriceRange,
} from '@/components/common';
import { TOTAL_COLOR, rangeSeries } from '@/utils/charts';
import { DEFAULT_FILTERS, DashboardFilterBar, toQuery } from '@/components/DashboardFilters';
import type { Filters } from '@/components/DashboardFilters';
import { TransportModeCard } from '@/components/TransportModeCard';
import { useAsync } from '@/hooks/useAsync';
import { dateOnly, dateTime, money, num, percent, TRANSPORT_LABEL } from '@/utils/format';
import { MIN_MAX_HINT, P25_P75_HINT } from '@/utils/hints';

const { Text, Title } = Typography;

/** Наблюдение считается свежим, если данные не старше суток мониторинга с запасом. */
const FRESH_DAYS = 2;

function SectionTitle({ children, hint }: { children: string; hint?: string }) {
  return (
    <div style={{ margin: '24px 0 12px' }}>
      <Title level={5} style={{ margin: 0 }}>
        {children}
      </Title>
      {hint ? (
        <Text type="secondary" style={{ fontSize: 13 }}>
          {hint}
        </Text>
      ) : null}
    </div>
  );
}

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
      // Границы размаха скрыты из легенды: это не самостоятельные ряды, а
      // коридор вокруг итога, и включать их отдельно нечего.
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
          color: TOTAL_COLOR,
        },
        ...rangeSeries(
          points.map((p) => p.min),
          points.map((p) => p.max),
        ),
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

  /**
   * Полнота наблюдений в бизнес-формулировке: по скольким направлениям данные
   * свежие. Считается на клиенте из last_update каждой строки — отдельного
   * агрегата на бэкенде для этого не нужно.
   */
  const freshness = useMemo(() => {
    const rows = directions.data?.items ?? [];
    const threshold = Date.now() - FRESH_DAYS * 24 * 60 * 60 * 1000;
    const fresh = rows.filter((row) => {
      if (!row.last_update) return false;
      const parsed = new Date(row.last_update).getTime();
      return !Number.isNaN(parsed) && parsed >= threshold;
    }).length;
    return { fresh, total: rows.length };
  }, [directions.data]);

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
      title: <LabelWithHint text="P25 – P75" hint={P25_P75_HINT} />,
      key: 'iqr',
      align: 'right' as const,
      render: (_: unknown, row: DirectionRow) => (
        <PriceRange low={row.p25 as number} high={row.p75 as number} />
      ),
    },
    {
      title: <LabelWithHint text="min – max" hint={MIN_MAX_HINT} />,
      key: 'range',
      align: 'right' as const,
      render: (_: unknown, row: DirectionRow) => (
        <PriceRange low={row.min as number} high={row.max as number} />
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
      title: '1 день',
      dataIndex: 'change_1d',
      align: 'right' as const,
      sorter: (a: DirectionRow, b: DirectionRow) => (a.change_1d ?? 0) - (b.change_1d ?? 0),
      render: (value: number | null) => <ChangeIndicator value={value} />,
    },
    {
      title: '7 дней',
      dataIndex: 'change_7d',
      align: 'right' as const,
      render: (value: number | null) => <ChangeIndicator value={value} />,
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

      <DashboardFilterBar value={filters} onChange={setFilters} />

      <SectionTitle hint="Медианы по всем направлениям, попавшим в фильтр">
        Сколько стоит поездка
      </SectionTitle>

      <AsyncBlock loading={overview.loading} error={overview.error}>
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Типовая стоимость"
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
              title="Изменение за 1 день"
              value={<ChangeIndicator value={data?.change_1d as number} />}
              hint="Сравнение с последним расчетом предыдущих суток по одному и тому же множеству сценариев: иначе изменение отражало бы смену состава, а не динамику рынка."
              extra={
                <Text type="secondary">
                  сопоставимых сценариев: {num(data?.comparable_scenarios_1d as number)}
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Доля транспорта"
              value={percent(data?.median_transport_share as number)}
              hint="Сколько в типовой стоимости приходится на проезд, а не на проживание."
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MetricCard
              title="Направлений под наблюдением"
              value={num(data?.scenario_count as number)}
              extra={
                <Text type="secondary">
                  с полной оценкой: {num(data?.complete_count as number)} (
                  {percent(data?.complete_rate as number, 0)})
                </Text>
              }
            />
          </Col>
        </Row>
      </AsyncBlock>

      <SectionTitle hint="Динамика типовой стоимости и ее состав">
        Как цена меняется
      </SectionTitle>

      <Row gutter={[16, 16]}>
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

      <Card title="Сравнение направлений" size="small" style={{ marginTop: 16 }}>
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
            scroll={{ x: 1100 }}
          />
        </AsyncBlock>
      </Card>

      <Card title="Существенные изменения за 7 дней" size="small" style={{ marginTop: 16 }}>
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
              {
                // Эндпоинт /dashboard/changes отдает направление как
                // origin/destination — названия городов, а не коды.
                title: 'Направление',
                key: 'route',
                render: (_: unknown, row: DirectionRow) => (
                  <span>
                    {row.origin ?? '—'} → {row.destination ?? '—'}{' '}
                    {row.transport_type ? (
                      <Tag>{TRANSPORT_LABEL[row.transport_type] ?? row.transport_type}</Tag>
                    ) : null}
                  </span>
                ),
              },
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

      <SectionTitle hint="Только те направления, где наблюдаются оба способа проезда">
        Авиа против ЖД
      </SectionTitle>

      {/* Фильтр по транспорту сюда не передается: он спрятал бы половину сравнения. */}
      <TransportModeCard query={{ ...query, transport_type: undefined }} />

      <SectionTitle hint="Насколько данные под наблюдением актуальны">
        Полнота наблюдений
      </SectionTitle>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={8}>
          <MetricCard
            title="Данные обновлены"
            value={data?.last_update ? dateOnly(data.last_update as string) : '—'}
            extra={
              <Text type="secondary">
                {data?.last_update ? dateTime(data.last_update as string) : 'наблюдений еще нет'}
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <MetricCard
            title="Направлений со свежими данными"
            value={
              freshness.total
                ? `${num(freshness.fresh)} из ${num(freshness.total)}`
                : '—'
            }
            hint={`Свежими считаются данные не старше ${FRESH_DAYS} дней.`}
            extra={
              freshness.total && freshness.fresh < freshness.total ? (
                <Text type="warning">
                  по {num(freshness.total - freshness.fresh)} направлениям данные устарели
                </Text>
              ) : (
                <Text type="secondary">все наблюдения актуальны</Text>
              )
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <MetricCard
            title="Расчетов с полной оценкой"
            value={percent(data?.complete_rate as number, 0)}
            hint="Полная оценка — это расчет, где определены и проезд, и проживание."
            extra={
              <Text type="secondary">
                {num(data?.complete_count as number)} из {num(data?.scenario_count as number)}
              </Text>
            }
          />
        </Col>
      </Row>
    </>
  );
}
