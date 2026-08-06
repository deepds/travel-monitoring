/**
 * Витрина вариантов отдыха по пяти ключевым городам.
 *
 * Отвечает на вопрос туриста, а не аналитика: куда поехать из своего города и
 * сколько это будет стоить. Поэтому здесь нет ни качества данных, ни
 * уверенности, ни разброса — только цена и ее состав.
 *
 * Старый дашборд остается на месте: он про наблюдение рынка, а этот про выбор.
 */

import {
  Alert, App, Button, Card, Col, DatePicker, Empty, Row, Segmented, Select, Space, Table, Tag,
  Typography,
} from 'antd';
import ReactECharts from 'echarts-for-react';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';

import { ApiError, api } from '@/api/client';
import type { ShowcaseOption } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { AsyncBlock, MetricDisclaimer, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { useTheme } from '@/theme/ThemeContext';
import { applyChartTheme } from '@/utils/charts';
import { dateAxis, money } from '@/utils/format';

const { Text, Title } = Typography;

/** Длительность поездки в сетке наблюдений. */
const GRID_NIGHTS = 5;

const TRANSPORT_OPTIONS = [
  { label: 'Поезд', value: 'RAIL' },
  { label: 'Самолет', value: 'AVIA' },
];

const STARS_OPTIONS = [
  { label: '3 звезды', value: '3' },
  { label: '4 звезды', value: '4' },
  { label: '5 звезд', value: '5' },
];

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

/** Состояния задачи, после которых ждать больше нечего. */
const TERMINAL = new Set(['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL']);

export default function ShowcasePage() {
  const { resolved } = useTheme();
  const { can } = useAuth();
  const { message } = App.useApp();
  const [calculating, setCalculating] = useState(false);
  const cities = useAsync(() => api.showcaseCities(), []);
  const cityOptions = useMemo(
    () => (cities.data?.items ?? []).map((item) => ({ label: item.name, value: item.code })),
    [cities.data],
  );

  const [origin, setOrigin] = useState('MOW');
  const [transport, setTransport] = useState('RAIL');
  const [stars, setStars] = useState('3');
  const [departure, setDeparture] = useState<Dayjs>(() => dayjs().add(14, 'day'));
  const [ret, setRet] = useState<Dayjs>(() => dayjs().add(14 + GRID_NIGHTS, 'day'));

  const departureKey = departure.format('YYYY-MM-DD');
  const returnKey = ret.format('YYYY-MM-DD');

  const options = useAsync(
    () =>
      api.showcaseOptions({
        origin,
        departure_date: departureKey,
        return_date: returnKey,
        transport_type: transport,
        stars,
      }),
    [origin, departureKey, returnKey, transport, stars],
  );

  const transportCurve = useAsync(
    () => api.showcaseTransportCurve({ origin }),
    [origin],
  );
  const stayCurve = useAsync(() => api.showcaseAccommodationCurve({ stars: '3' }), []);

  /** Дата отправления задает и дату возвращения: длительность в сетке одна. */
  const onDeparture = (value: Dayjs | null) => {
    if (!value) return;
    setDeparture(value);
    if (!ret.isAfter(value)) setRet(value.add(GRID_NIGHTS, 'day'));
  };

  const curveOption = useMemo(() => {
    const series = transportCurve.data?.series ?? [];
    const dates = Array.from(
      new Set(series.flatMap((item) => item.points.map((point) => point.departure_date))),
    ).sort();
    return {
      tooltip: { trigger: 'axis', valueFormatter: (value: number) => money(value) },
      legend: { data: series.map((item) => item.destination_name), bottom: 0 },
      grid: { left: 8, right: 16, top: 24, bottom: 48, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates.map((item) => dateAxis(item)),
        axisLabel: { interval: Math.max(0, Math.floor(dates.length / 10)), rotate: 45 },
      },
      yAxis: { type: 'value', axisLabel: { formatter: (value: number) => money(value) } },
      series: series.map((item) => ({
        name: item.destination_name,
        type: 'line',
        smooth: true,
        connectNulls: false,
        data: dates.map(
          (day) => item.points.find((point) => point.departure_date === day)?.price ?? null,
        ),
      })),
    };
  }, [transportCurve.data]);

  const stayOption = useMemo(() => {
    const series = stayCurve.data?.series ?? [];
    const dates = Array.from(
      new Set(series.flatMap((item) => item.points.map((point) => point.check_in))),
    ).sort();
    return {
      tooltip: { trigger: 'axis', valueFormatter: (value: number) => money(value) },
      legend: { data: series.map((item) => item.city_name), bottom: 0 },
      grid: { left: 8, right: 16, top: 24, bottom: 48, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates.map((item) => dateAxis(item)),
        axisLabel: { interval: Math.max(0, Math.floor(dates.length / 10)), rotate: 45 },
      },
      yAxis: { type: 'value', axisLabel: { formatter: (value: number) => money(value) } },
      series: series.map((item) => ({
        name: item.city_name,
        type: 'line',
        smooth: true,
        connectNulls: false,
        data: dates.map(
          (day) => item.points.find((point) => point.check_in === day)?.price ?? null,
        ),
      })),
    };
  }, [stayCurve.data]);

  const emptyOptions = (options.data?.items ?? []).every((item) => item.total === null);

  /**
   * Разовый расчет для дат вне сетки.
   *
   * Считается тем же профилем, что и вся витрина: иначе его цифра
   * несопоставима с соседними и разница будет выглядеть движением рынка.
   * Ждем завершения задач, а не просто ставим их: без ожидания пользователь
   * увидит ту же пустую таблицу и решит, что кнопка не сработала.
   */
  const runOnDemand = async () => {
    const destinations = (options.data?.items ?? []).map((item) => item.destination_code);
    if (!destinations.length) return;

    setCalculating(true);
    try {
      const jobs = await Promise.all(
        destinations.map((destination) =>
          api.createCalculation({
            profile_id: options.data?.profile_id ?? undefined,
            scenario: {
              origin_city_code: origin,
              destination_city_code: destination,
              departure_date: departureKey,
              return_date: returnKey,
              adults: 1,
              transport_type: transport,
              flight_fare_type: transport === 'AVIA' ? 'CHEAPEST' : null,
              rail_class: transport === 'RAIL' ? 'COMPARTMENT' : null,
              accommodation_type: 'HOTEL',
              stars,
              scenario_type: 'ON_DEMAND',
            },
          }),
        ),
      );

      const pending = jobs.map((job: any) => job.job_id).filter(Boolean);
      for (let attempt = 0; attempt < 40 && pending.length; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const states = await Promise.all(pending.map((id: string) => api.calculation(id)));
        if (states.every((job) => TERMINAL.has(job.status))) break;
      }
      options.reload();
    } catch (error) {
      message.error(
        error instanceof ApiError ? error.message : 'Не удалось запустить расчет',
      );
    } finally {
      setCalculating(false);
    }
  };

  return (
    <>
      <PageTitle
        title="Куда поехать"
        subtitle="Варианты отдыха из пяти ключевых городов на одни и те же даты"
      />

      <MetricDisclaimer text={options.data?.disclaimer} />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} align="bottom">
          <Col xs={24} md={6}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Откуда
            </Text>
            <Select
              value={origin}
              onChange={setOrigin}
              options={cityOptions}
              loading={cities.loading}
              style={{ width: '100%' }}
            />
          </Col>
          <Col xs={12} md={5}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Туда
            </Text>
            <DatePicker
              value={departure}
              onChange={onDeparture}
              allowClear={false}
              format="DD.MM.YYYY"
              style={{ width: '100%' }}
            />
          </Col>
          <Col xs={12} md={5}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Обратно
            </Text>
            <DatePicker
              value={ret}
              onChange={(value) => value && setRet(value)}
              allowClear={false}
              format="DD.MM.YYYY"
              style={{ width: '100%' }}
            />
          </Col>
          <Col xs={12} md={4}>
            <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
              Транспорт
            </Text>
            <Segmented
              value={transport}
              onChange={(value) => setTransport(String(value))}
              options={TRANSPORT_OPTIONS}
              block
            />
          </Col>
          <Col xs={12} md={4}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Отель
            </Text>
            <Select
              value={stars}
              onChange={setStars}
              options={STARS_OPTIONS}
              style={{ width: '100%' }}
            />
          </Col>
        </Row>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          Один человек. Наблюдения ведутся на {GRID_NIGHTS} ночей вперед на месяц —
          для других дат данных может не быть.
        </Text>
      </Card>

      <AsyncBlock loading={options.loading} error={options.error} minHeight={200}>
        {emptyOptions ? (
          <Card size="small">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space direction="vertical" size={8}>
                  <span>
                    На эти даты наблюдений нет: сетка покрывает месяц вперед, поездку
                    длиной {GRID_NIGHTS} ночей.
                  </span>
                  {can('ANALYST') ? (
                    <Button type="primary" loading={calculating} onClick={runOnDemand}>
                      Рассчитать эти даты
                    </Button>
                  ) : (
                    <Text type="secondary">
                      Разовый расчет доступен аналитику.
                    </Text>
                  )}
                  {calculating ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      Идет обращение к источникам по четырем направлениям, это занимает
                      до минуты.
                    </Text>
                  ) : null}
                </Space>
              }
            />
          </Card>
        ) : (
          <Table<ShowcaseOption>
            size="small"
            rowKey="destination_code"
            dataSource={options.data?.items ?? []}
            pagination={false}
            scroll={{ x: 620 }}
            columns={[
              {
                title: 'Куда',
                dataIndex: 'destination_name',
                // Разовый расчет помечается: это один снимок по запросу, а не
                // наблюдение сетки, и рядом с наблюдавшимися числами он должен
                // быть отличим.
                render: (value: string, row) => (
                  <Space size={6}>
                    <b>{value}</b>
                    {row.on_demand ? (
                      <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                        разовый расчёт
                      </Tag>
                    ) : null}
                  </Space>
                ),
              },
              {
                title: 'Транспорт',
                dataIndex: 'transport',
                align: 'right',
                render: (value: number | null) =>
                  value === null ? <Text type="secondary">нет данных</Text> : money(value),
              },
              {
                title: 'Проживание',
                dataIndex: 'accommodation',
                align: 'right',
                render: (value: number | null) =>
                  value === null ? <Text type="secondary">нет данных</Text> : money(value),
              },
              {
                title: 'Итого',
                dataIndex: 'total',
                align: 'right',
                sorter: (a, b) => (a.total ?? Infinity) - (b.total ?? Infinity),
                defaultSortOrder: 'ascend',
                render: (value: number | null) =>
                  value === null ? <Text type="secondary">—</Text> : <b>{money(value)}</b>,
              },
            ]}
          />
        )}
      </AsyncBlock>

      <SectionTitle hint="Как меняется цена в зависимости от того, когда выезжать. Поезд, в одну сторону">
        Цена проезда по датам отправления
      </SectionTitle>

      <Card size="small">
        <AsyncBlock
          loading={transportCurve.loading}
          error={transportCurve.error}
          empty={(transportCurve.data?.series ?? []).every((item) => item.points.length === 0)}
          emptyText="Наблюдений по этому городу еще нет"
          minHeight={320}
        >
          <ReactECharts
            option={applyChartTheme(curveOption, resolved)}
            style={{ height: 340 }}
            notMerge
          />
        </AsyncBlock>
      </Card>

      <SectionTitle hint="Медиана за пять ночей, категория 3 звезды">
        Проживание по датам заезда
      </SectionTitle>

      <Card size="small">
        <AsyncBlock
          loading={stayCurve.loading}
          error={stayCurve.error}
          empty={(stayCurve.data?.series ?? []).every((item) => item.points.length === 0)}
          emptyText="Наблюдений по проживанию еще нет"
          minHeight={320}
        >
          <ReactECharts
            option={applyChartTheme(stayOption, resolved)}
            style={{ height: 340 }}
            notMerge
          />
        </AsyncBlock>
      </Card>

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 16 }}
        message="Это наблюдение рынка, а не бронирование"
        description="Показаны медианы предложений, наблюдавшихся в последнем срезе. Цена конкретного билета или номера в момент покупки будет отличаться, а наличие мест не гарантировано."
      />
    </>
  );
}
