/**
 * Аналитика, работающая на срезе без накопленной истории.
 *
 * Все четыре виджета отвечают на вопросы, которые можно задать данным уже
 * сегодня: когда ехать, сколько берет агент, из чего складывается цена и
 * стоит ли перебирать варианты. Динамика во времени сюда не входит — для
 * нее нужны недели наблюдений.
 */

import { Alert, Card, Col, Empty, Row, Statistic, Table, Tooltip, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useMemo } from 'react';

import { api } from '@/api/client';
import type { Premium, SourceGapRow, SpreadRow } from '@/api/types';
import { AsyncBlock, RouteCell } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { useTheme } from '@/theme/ThemeContext';
import { applyChartTheme, chartTheme } from '@/utils/charts';
import {
  dateAxis, dateOnly, money, num, signedPercent, sourceLabel,
} from '@/utils/format';

const { Text } = Typography;

type Query = Record<string, string | number | boolean | null | undefined>;

const deps = (query?: Query) => [JSON.stringify(query ?? {})];

function route(row: { origin_city_name: string; destination_city_name: string }) {
  return `${row.origin_city_name} → ${row.destination_city_name}`;
}

/** Цена по датам вылета — индекс к типичной стоимости своего направления. */
export function DepartureDatesCard({ query }: { query?: Query }) {
  const { resolved } = useTheme();
  const data = useAsync(() => api.departureDates(query), deps(query));
  const points = data.data?.points ?? [];

  /**
   * Дороже и дешевле типичного различаются теплым и холодным цветом общей
   * палитры, а не красным и зеленым: те читаются как «плохо» и «хорошо», хотя
   * здесь это просто положение относительно медианы.
   */
  const [cheaper, pricier] = chartTheme(resolved).palette;

  const option = useMemo(
    () => ({
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          const p = params[0];
          const point = points[p.dataIndex];
          return [
            `<b>${dateOnly(point.departure_date)}</b>`,
            `Индекс: ${point.price_index.toFixed(2)}`,
            `Медиана: ${money(point.median_total_cost)}`,
            `Сценариев: ${point.scenario_count} на ${point.routes} маршрутах`,
          ].join('<br/>');
        },
      },
      // containLabel вместо фиксированного отступа снизу: повернутым датам
      // нужно больше места, чем оставлял отступ, и подписи обрезались.
      grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: points.map((p) => dateAxis(p.departure_date)),
        axisLabel: { interval: 0, rotate: points.length > 6 ? 45 : 0 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { formatter: (v: number) => `${Math.round(v * 100)} %` },
      },
      series: [
        {
          type: 'bar',
          data: points.map((p) => ({
            value: p.price_index,
            itemStyle: { color: p.price_index >= 1 ? pricier : cheaper },
          })),
          markLine: {
            silent: true,
            symbol: 'none',
            data: [{ yAxis: 1, label: { formatter: 'типичная' } }],
          },
        },
      ],
    }),
    [points, cheaper, pricier],
  );

  return (
    <Card size="small" title="Цена по датам вылета">
      <AsyncBlock loading={data.loading} error={data.error}>
        {points.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="Нужны маршруты, наблюдаемые на нескольких датах вылета"
          />
        ) : (
          <>
            <Text type="secondary">
              Отношение к типичной стоимости своего направления по {data.data?.comparable_routes}{' '}
              сопоставимым маршрутам. Теплый цвет — дороже типичной, холодный — дешевле.
            </Text>
            <ReactECharts
              option={applyChartTheme(option, resolved)}
              style={{ height: 300, marginTop: 8 }}
              notMerge
            />
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 8 }}
              message="Это не кривая бронирования"
              description="При одном срезе наблюдения дата вылета и глубина бронирования связаны линейно, поэтому «дорожает к дате» и «этот месяц дороже» здесь неразделимы. Разделить их можно, когда накопится история наблюдений одной и той же даты."
            />
          </>
        )}
      </AsyncBlock>
    </Card>
  );
}

/** Разрыв цен между агентом и перевозчиком по одним и тем же поездам. */
export function SourceGapCard({ query }: { query?: Query }) {
  const data = useAsync(() => api.sourceGap(query), deps(query));
  const items = data.data?.items ?? [];
  const summary = data.data?.summary;

  return (
    <Card size="small" title="Переплата у агента">
      <AsyncBlock loading={data.loading} error={data.error}>
        {items.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="Нет поездов, по которым оба источника дали цену"
          />
        ) : (
          <>
            <Row gutter={16} style={{ marginBottom: 12 }}>
              <Col span={12}>
                <Statistic
                  title="Типичная переплата"
                  value={summary?.median_gap != null ? summary.median_gap * 100 : 0}
                  precision={1}
                  suffix="%"
                  valueStyle={{ color: '#cf1322' }}
                />
              </Col>
              <Col span={12}>
                <Statistic title="Сопоставлено поездов" value={summary?.matched_trains ?? 0} />
              </Col>
            </Row>

            <Table<SourceGapRow>
              size="small"
              rowKey={(row) => `${row.origin_city_code}-${row.destination_city_code}`}
              dataSource={items}
              pagination={false}
              scroll={{ x: 520 }}
              columns={[
                { title: 'Направление', key: 'route', render: (_, row) => route(row) },
                {
                  title: 'Поездов',
                  dataIndex: 'matched_trains',
                  align: 'right',
                  width: 90,
                  render: (value: number) => num(value),
                },
                {
                  title: 'Типичная',
                  dataIndex: 'median_gap',
                  align: 'right',
                  width: 110,
                  render: (value: number) => <Text type="danger">{signedPercent(value)}</Text>,
                },
                {
                  title: 'Наибольшая',
                  dataIndex: 'max_gap',
                  align: 'right',
                  width: 120,
                  render: (value: number) => signedPercent(value),
                },
              ]}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {sourceLabel('rzd')} — перевозчик, {sourceLabel('tutu_mcp')} — агент. Цена агента
              предкорзинная, поэтому реальный разрыв больше.
            </Text>
          </>
        )}
      </AsyncBlock>
    </Card>
  );
}

function PremiumTile({ item, hint }: { item: Premium; hint: string }) {
  const value = item.premium;
  return (
    <Card size="small">
      <Tooltip title={hint}>
        <Text type="secondary">
          {item.from} → {item.to}
        </Text>
      </Tooltip>
      <div
        style={{
          fontSize: 24,
          fontWeight: 600,
          color: value == null ? undefined : value > 0 ? '#cf1322' : '#3f8600',
        }}
      >
        {value == null ? '—' : signedPercent(value)}
      </div>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {item.base != null ? `${money(item.base)} → ${money(item.upgraded)}` : 'нет пар'}
        {item.snapshots ? ` · ${item.snapshots} набл.` : ''}
      </Text>
    </Card>
  );
}

/** Надбавки: за звезду, за багаж, за отсутствие пересадки. */
export function PriceCompositionCard({ query }: { query?: Query }) {
  const data = useAsync(() => api.priceComposition(query), deps(query));
  const d = data.data;

  return (
    <Card size="small" title="Из чего складывается цена">
      <AsyncBlock loading={data.loading} error={data.error}>
        <Row gutter={[12, 12]}>
          <Col xs={24} md={8}>
            <PremiumTile
              item={d?.stars ?? { from: '3★', to: '4★', premium: null, base: null, upgraded: null, snapshots: 0 }}
              hint="Насколько дороже размещение категорией выше при тех же датах и городе."
            />
          </Col>
          <Col xs={24} md={8}>
            <PremiumTile
              item={d?.baggage ?? { from: 'Ручная кладь', to: 'С багажом', premium: null, base: null, upgraded: null, snapshots: 0 }}
              hint="Сравниваются тарифы целиком: тариф с багажом обычно отличается и условиями обмена."
            />
          </Col>
          <Col xs={24} md={8}>
            <PremiumTile
              item={d?.direct ?? { from: 'С пересадкой', to: 'Прямой', premium: null, base: null, upgraded: null, snapshots: 0 }}
              hint="Отрицательное значение означает, что прямой рейс дешевле пересадочного."
            />
          </Col>
        </Row>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          Считается парно внутри каждого наблюдения, поэтому состав выборки на надбавку не влияет.
        </Text>
      </AsyncBlock>
    </Card>
  );
}

/** Разброс цен внутри направления и рейтинг размещения. */
export function SpreadCard({ query }: { query?: Query }) {
  const data = useAsync(() => api.spread(query), deps(query));
  const items = data.data?.items ?? [];
  const summary = data.data?.summary;

  return (
    <Card size="small" title="Стоит ли перебирать варианты">
      <AsyncBlock loading={data.loading} error={data.error} empty={items.length === 0}>
        <Text type="secondary">
          Типичный разброс — {summary?.median_iqr_ratio ?? '—'}× между дешевой и дорогой
          четвертью предложений.
        </Text>
        <Table<SpreadRow>
          size="small"
          style={{ marginTop: 8 }}
          rowKey={(row) => `${row.origin_city_code}-${row.destination_city_code}-${row.transport_type}`}
          dataSource={items}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          scroll={{ x: 900 }}
          columns={[
            {
              title: 'Направление',
              key: 'route',
              width: 300,
              render: (_, row) => (
                <RouteCell
                  origin={row.origin_city_name}
                  destination={row.destination_city_name}
                  transport={row.transport_type}
                />
              ),
            },
            {
              title: 'Разброс',
              dataIndex: 'iqr_ratio',
              align: 'right',
              width: 100,
              sorter: (a, b) => (a.iqr_ratio ?? 0) - (b.iqr_ratio ?? 0),
              render: (value: number | null) =>
                value == null ? '—' : <b>{value.toFixed(2)}×</b>,
            },
            {
              title: 'P25 – P75',
              key: 'iqr',
              align: 'right',
              width: 190,
              render: (_, row) => (
                <Text type="secondary">
                  {money(row.p25)} — {money(row.p75)}
                </Text>
              ),
            },
            {
              title: 'Рейтинг',
              dataIndex: 'review_score',
              align: 'right',
              width: 100,
              render: (value: number | null, row) =>
                value == null ? (
                  '—'
                ) : (
                  <Tooltip title={`по ${num(row.review_sample)} отзывам`}>
                    <span>{value.toFixed(2)}</span>
                  </Tooltip>
                ),
            },
            {
              title: 'Границы',
              key: 'range',
              align: 'right',
              width: 190,
              render: (_, row) => (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {money(row.min)} — {money(row.max)}
                </Text>
              ),
            },
          ]}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          Разброс считается по P25–P75: он устойчив, тогда как границы min–max задает единственный
          самый дорогой вариант. Доля транспорта и проживания — в таблице направлений выше.
        </Text>
      </AsyncBlock>
    </Card>
  );
}
