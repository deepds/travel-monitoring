/**
 * Витрина вариантов отдыха по пяти ключевым городам — главная страница.
 *
 * Отвечает на вопрос туриста, а не аналитика: куда поехать из своего города и
 * сколько это будет стоить.
 *
 * ДВЕ ЦИФРЫ ВМЕСТО ОДНОЙ
 *
 * Медиана систематически выше того, что платит покупатель: человек едет
 * туда-обратно одним поездом и берет из дешевой половины выдачи. Поэтому
 * рядом с ней всегда стоит минимум — «от 12 400 ₽, типично 18 700 ₽». Это же
 * снимает главное расхождение с сайтом источника: сайт крупно показывает
 * «от», и пока рядом одна медиана, разница читается как ошибка.
 *
 * НИ ОДНОГО ТУПИКА
 *
 * Любое число кликабельно и раскрывается в разбор цены, из разбора — переход
 * к самим предложениям. Путь от цифры до первоисточника — три клика без ввода
 * параметров.
 */

import {
  Alert, App, Button, Card, Col, DatePicker, Empty, Row, Segmented, Select, Space, Table, Tag,
  Tooltip, Typography,
} from 'antd';
import ReactECharts from 'echarts-for-react';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import type { ShowcaseOption, ShowcasePrice } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { AsyncBlock, MetricDisclaimer, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { useTheme } from '@/theme/ThemeContext';
import { applyChartTheme } from '@/utils/charts';
import { dateAxis, money } from '@/utils/format';

const { Text, Title } = Typography;

/** Длительность поездки в сетке наблюдений транспорта. */
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

/** Какую из двух цифр рисовать линией: восемь линий на графике нечитаемы. */
const CURVE_MODE_OPTIONS = [
  { label: 'типичная', value: 'median' },
  { label: 'от', value: 'min' },
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

/**
 * Цена в таблице: «от» сверху, типичная под ней, размер выборки подписью.
 *
 * Размер выборки обязателен рядом с ценой: медиана по четырем предложениям и
 * медиана по сорока выглядят одинаково, а доверия заслуживают разного.
 */
function PriceCell({
  value,
  onOpen,
  strong,
  minLabel,
}: {
  value: ShowcasePrice | null;
  onOpen?: (runId: string) => void;
  strong?: boolean;
  minLabel?: string;
}) {
  if (!value) return <Text type="secondary">нет данных</Text>;
  const typical = strong ? <b>{money(value.median)}</b> : money(value.median);
  const clickable = Boolean(value.run_id && onOpen);

  const body = (
    <Space direction="vertical" size={0} align="end">
      {value.min !== null ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {minLabel ?? 'от'} {money(value.min)}
        </Text>
      ) : null}
      <span>{typical}</span>
      <Text type="secondary" style={{ fontSize: 11 }}>
        {value.offers} предл. · {value.sources} ист.
        {value.estimated ? ' · оценка' : ''}
      </Text>
    </Space>
  );

  if (!clickable) return body;
  return (
    <a onClick={() => onOpen!(value.run_id!)} style={{ display: 'inline-block' }}>
      {body}
    </a>
  );
}

/** Состояния задачи, после которых ждать больше нечего. */
const TERMINAL = new Set(['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL']);

export default function ShowcasePage() {
  const { resolved } = useTheme();
  const { can } = useAuth();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [calculating, setCalculating] = useState(false);

  const cities = useAsync(() => api.showcaseCities(), []);
  const cityOptions = useMemo(
    () => (cities.data?.items ?? []).map((item) => ({ label: item.name, value: item.code })),
    [cities.data],
  );

  const [origin, setOrigin] = useState('MOW');
  const [transport, setTransport] = useState('RAIL');
  const [stars, setStars] = useState('3');
  const [curveStars, setCurveStars] = useState('3');
  const [curveMode, setCurveMode] = useState<'median' | 'min'>('median');
  const [departure, setDeparture] = useState<Dayjs>(() => dayjs().add(14, 'day'));
  const [ret, setRet] = useState<Dayjs>(() => dayjs().add(14 + GRID_NIGHTS, 'day'));
  const [observationDate, setObservationDate] = useState<string | undefined>(undefined);

  const departureKey = departure.format('YYYY-MM-DD');
  const returnKey = ret.format('YYYY-MM-DD');
  const nights = Math.max(1, ret.diff(departure, 'day'));

  const dates = useAsync(() => api.showcaseObservationDates(), []);
  const dateOptions = useMemo(
    () =>
      (dates.data?.items ?? []).map((item) => ({
        label: dayjs(item.observation_date).format('DD.MM.YYYY'),
        value: item.observation_date,
      })),
    [dates.data],
  );

  const options = useAsync(
    () =>
      api.showcaseOptions({
        origin,
        departure_date: departureKey,
        return_date: returnKey,
        transport_type: transport,
        stars,
        ...(observationDate ? { observation_date: observationDate } : {}),
      }),
    [origin, departureKey, returnKey, transport, stars, observationDate],
  );

  const transportCurve = useAsync(
    () =>
      api.showcaseTransportCurve({
        origin,
        transport_type: transport,
        ...(observationDate ? { observation_date: observationDate } : {}),
      }),
    [origin, transport, observationDate],
  );

  // Проживание показывается по всем пяти городам независимо от выбора в блоке
  // выше: график отвечает на вопрос «когда в городе дорого», и свой город в
  // нем такой же ответ, как чужой. Своя звездность — по той же причине.
  const stayCurve = useAsync(
    () =>
      api.showcaseAccommodationCurve({
        stars: curveStars,
        ...(observationDate ? { observation_date: observationDate } : {}),
      }),
    [curveStars, observationDate],
  );

  /** Дата отправления не должна оказываться позже возвращения. */
  const onDeparture = (value: Dayjs | null) => {
    if (!value) return;
    setDeparture(value);
    if (!ret.isAfter(value)) setRet(value.add(GRID_NIGHTS, 'day'));
  };

  const openRun = (runId: string) => navigate(`/breakdown/${runId}`);

  const curveOption = useMemo(() => {
    const series = transportCurve.data?.series ?? [];
    const days = Array.from(
      new Set(series.flatMap((item) => item.points.map((point) => point.departure_date))),
    ).sort();
    return {
      // В подсказке обе цифры сразу: на графике линия одна, чтобы четыре
      // направления не превращались в восемь линий.
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          if (!params?.length) return '';
          const day = params[0].axisValue;
          const rows = params
            .map((item) => {
              const found = series
                .find((entry) => entry.destination_name === item.seriesName)
                ?.points.find((point) => dateAxis(point.departure_date) === day);
              if (!found) return '';
              const from = found.min !== null ? `от ${money(found.min)}, ` : '';
              return `${item.marker} ${item.seriesName}: ${from}типично ${money(
                found.price,
              )} · ${found.offers} предл.`;
            })
            .filter(Boolean);
          return [day, ...rows].join('<br/>');
        },
      },
      legend: { data: series.map((item) => item.destination_name), bottom: 0 },
      grid: { left: 8, right: 16, top: 24, bottom: 48, containLabel: true },
      xAxis: {
        type: 'category',
        data: days.map((item) => dateAxis(item)),
        axisLabel: { interval: Math.max(0, Math.floor(days.length / 10)), rotate: 45 },
      },
      yAxis: { type: 'value', axisLabel: { formatter: (value: number) => money(value) } },
      // Разрыв соединяется линией, но сглаживание выключено. Кружок стоит только
      // на наблюдавшейся дате, поэтому отрезок без кружков читается как «здесь
      // не наблюдали». Сплайн такого сказать не может: через длинный разрыв он
      // рисует размашистую дугу, и она выглядит движением цены, которого не было.
      series: series.map((item) => ({
        name: item.destination_name,
        type: 'line',
        smooth: false,
        connectNulls: true,
        data: days.map((day) => {
          const point = item.points.find((entry) => entry.departure_date === day);
          if (!point) return null;
          return curveMode === 'min' ? point.min : point.price;
        }),
      })),
    };
  }, [transportCurve.data, curveMode]);

  const stayOption = useMemo(() => {
    const series = stayCurve.data?.series ?? [];
    const days = Array.from(
      new Set(series.flatMap((item) => item.points.map((point) => point.check_in))),
    ).sort();
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          if (!params?.length) return '';
          const day = params[0].axisValue;
          const rows = params
            .map((item) => {
              const found = series
                .find((entry) => entry.city_name === item.seriesName)
                ?.points.find((point) => dateAxis(point.check_in) === day);
              if (!found) return '';
              const from = found.min !== null ? `от ${money(found.min)}, ` : '';
              return `${item.marker} ${item.seriesName}: ${from}типично ${money(
                found.price,
              )} · ${found.offers} предл.`;
            })
            .filter(Boolean);
          return [day, ...rows].join('<br/>');
        },
      },
      legend: { data: series.map((item) => item.city_name), bottom: 0 },
      grid: { left: 8, right: 16, top: 24, bottom: 48, containLabel: true },
      xAxis: {
        type: 'category',
        data: days.map((item) => dateAxis(item)),
        axisLabel: { interval: Math.max(0, Math.floor(days.length / 10)), rotate: 45 },
      },
      yAxis: { type: 'value', axisLabel: { formatter: (value: number) => money(value) } },
      series: series.map((item) => ({
        name: item.city_name,
        type: 'line',
        smooth: false,
        connectNulls: true,
        data: days.map((day) => {
          const point = item.points.find((entry) => entry.check_in === day);
          if (!point) return null;
          return curveMode === 'min' ? point.min : point.price;
        }),
      })),
    };
  }, [stayCurve.data, curveMode]);

  /**
   * Подпись графика проезда.
   *
   * Единица измерения зависит от вида проезда, и об этом надо сказать прямо:
   * у ЖД источник отдает цену плеча своим полем, поэтому кривая про поездку в
   * одну сторону, а авиабилет продается круговым тарифом одним числом, и
   * делить его на плечи значило бы выдумывать.
   */
  const curveHint = useMemo(() => {
    const mode = transport === 'AVIA' ? 'Самолет' : 'Поезд';
    const direction =
      transportCurve.data?.direction === 'ROUND_TRIP' ? 'туда-обратно' : 'в одну сторону';
    return `Как меняется цена в зависимости от того, когда выезжать. ${mode}, ${direction}, один человек`;
  }, [transport, transportCurve.data]);

  const emptyOptions = (options.data?.items ?? []).every((item) => item.total === null);
  const anyEstimated = (options.data?.items ?? []).some(
    (item) => item.accommodation?.estimated,
  );

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
        subtitle="Варианты отдыха из пяти ключевых городов на выбранные даты"
      />

      <MetricDisclaimer text={options.data?.disclaimer} />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} align="bottom">
          <Col xs={24} md={5}>
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
          <Col xs={12} md={4}>
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
          <Col xs={12} md={4}>
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
          <Col xs={12} md={3}>
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
          <Col xs={12} md={4}>
            {/* Наблюдения не переписываются, поэтому вчерашний срез никуда не
                делся. Список дат нужен затем, чтобы пустой график был отличим
                от дня, в который просто не наблюдали. */}
            <Text type="secondary" style={{ fontSize: 12 }}>
              Срез наблюдений
            </Text>
            <Select
              value={observationDate}
              onChange={setObservationDate}
              options={dateOptions}
              loading={dates.loading}
              allowClear
              placeholder="последний"
              style={{ width: '100%' }}
            />
          </Col>
        </Row>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          Один человек, {nights} ноч. Проезд наблюдается на {GRID_NIGHTS} ночей —
          на других датах возврата цифры может не быть. Проживание считается из
          цены одной ночи.
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
                    На эти даты наблюдений нет: сетка покрывает месяц вперед, проезд —
                    поездку длиной {GRID_NIGHTS} ночей.
                  </span>
                  {can('ANALYST') ? (
                    <Button type="primary" loading={calculating} onClick={runOnDemand}>
                      Рассчитать эти даты
                    </Button>
                  ) : (
                    <Text type="secondary">Разовый расчет доступен аналитику.</Text>
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
          <>
            <Table<ShowcaseOption>
              size="small"
              rowKey="destination_code"
              dataSource={options.data?.items ?? []}
              pagination={false}
              scroll={{ x: 720 }}
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
                  render: (value: ShowcasePrice | null) => (
                    <PriceCell value={value} onOpen={openRun} />
                  ),
                },
                {
                  title: `Проживание, ${nights} ноч.`,
                  dataIndex: 'accommodation',
                  align: 'right',
                  render: (value: ShowcasePrice | null) => (
                    <PriceCell value={value} onOpen={openRun} />
                  ),
                },
                {
                  title: 'Итого',
                  dataIndex: 'total',
                  align: 'right',
                  sorter: (a, b) =>
                    (a.total?.median ?? Infinity) - (b.total?.median ?? Infinity),
                  defaultSortOrder: 'ascend',
                  render: (value: ShowcasePrice | null) =>
                    value ? (
                      <Tooltip title="«Не дешевле чем» — сумма самого дешевого билета и самого дешевого отеля. Такую пару можно и не собрать: дешевый рейс бывает в неудобное время, а дешевый отель к тому моменту разберут.">
                        <span>
                          <PriceCell value={value} strong minLabel="не дешевле" />
                        </span>
                      </Tooltip>
                    ) : (
                      <Text type="secondary">—</Text>
                    ),
                },
              ]}
            />
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
              «от» — самое дешевое из наблюдавшихся предложений, а не гарантия наличия
              мест по этой цене. Клик по цене открывает разбор: из чего она сложилась и
              какие предложения в нее вошли.
              {anyEstimated
                ? ' Проживание на выбранный срок — оценка «цена ночи × число ночей»: человек платит за бронь, а короткие брони отели продают дороже.'
                : ''}
            </Text>
          </>
        )}
      </AsyncBlock>

      <SectionTitle hint={curveHint}>Цена проезда по датам отправления</SectionTitle>

      <Card
        size="small"
        extra={
          <Segmented
            size="small"
            value={curveMode}
            onChange={(value) => setCurveMode(value as 'median' | 'min')}
            options={CURVE_MODE_OPTIONS}
          />
        }
      >
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

      <SectionTitle hint="Медиана за одну ночь на одного человека. Одна точка — одна ночь и один день недели, поэтому видно, когда в городе дорого">
        Проживание по датам заезда
      </SectionTitle>

      <Card
        size="small"
        extra={
          <Space size={8}>
            <Select
              size="small"
              value={curveStars}
              onChange={setCurveStars}
              options={STARS_OPTIONS}
              style={{ width: 120 }}
            />
            <Segmented
              size="small"
              value={curveMode}
              onChange={(value) => setCurveMode(value as 'median' | 'min')}
              options={CURVE_MODE_OPTIONS}
            />
          </Space>
        }
      >
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
        description="Показаны предложения, наблюдавшиеся в выбранном срезе. Цена конкретного билета или номера в момент покупки будет отличаться, а наличие мест не гарантировано. В итог входят только проезд и жилье: питание, экскурсии и городской транспорт не учитываются."
      />
    </>
  );
}
