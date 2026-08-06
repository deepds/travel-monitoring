/**
 * Покрытие и качество: где дыры и чему верить.
 *
 * Матрица «маршрут × дата» и «город × дата» показывает состояние каждого
 * наблюдения цветом, а размер выборки — числом. Дыры должны быть видны глазом,
 * а не вычитываться из графика: пока их приходилось искать запросами к базе,
 * очередной дефект находился случайно и через неделю.
 *
 * Рядом — итог суточного прогона: сколько сценариев было в плане, сколько
 * собралось, что помешало. Прогон, потерявший больше 5 % сценариев, виден без
 * чтения логов.
 */

import { Alert, Card, Col, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd';
import dayjs from 'dayjs';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '@/api/client';
import type { CoverageCell } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { money, num, percent } from '@/utils/format';

const { Text, Paragraph } = Typography;

/** Цвет клетки. Пусто — это тоже ответ, и у каждого «пусто» своя причина. */
const CELL_COLOR: Record<CoverageCell['state'], string> = {
  OK: '#3f8600',
  THIN: '#d48806',
  PARTIAL: '#d46b08',
  NO_DATA: '#8c8c8c',
  NOT_OBSERVED: '#bfbfbf',
};

const CELL_GLYPH: Record<CoverageCell['state'], string> = {
  OK: '●',
  THIN: '◐',
  PARTIAL: '◑',
  NO_DATA: '○',
  NOT_OBSERVED: '·',
};

function Cell({ cell, onOpen }: { cell: CoverageCell; onOpen: (id: string) => void }) {
  const title = [
    dayjs(cell.date).format('DD.MM'),
    cell.price !== null ? money(cell.price) : 'цифры нет',
    `${cell.offers} предл. · ${cell.sources} ист.`,
  ].join(' · ');

  const glyph = (
    <span style={{ color: CELL_COLOR[cell.state], fontSize: 14, lineHeight: '14px' }}>
      {CELL_GLYPH[cell.state]}
    </span>
  );

  return (
    <Tooltip title={title}>
      {cell.run_id ? (
        <a onClick={() => onOpen(cell.run_id!)} style={{ padding: '0 1px' }}>
          {glyph}
        </a>
      ) : (
        <span style={{ padding: '0 1px' }}>{glyph}</span>
      )}
    </Tooltip>
  );
}

function MatrixRow({ cells, onOpen }: { cells: CoverageCell[]; onOpen: (id: string) => void }) {
  return (
    <Space size={2} wrap={false}>
      {cells.map((cell) => (
        <Cell key={cell.date} cell={cell} onOpen={onOpen} />
      ))}
    </Space>
  );
}

export default function CoveragePage() {
  const navigate = useNavigate();
  const [observationDate, setObservationDate] = useState<string | undefined>(undefined);

  const dates = useAsync(() => api.showcaseObservationDates(), []);
  const coverage = useAsync(
    () =>
      api.showcaseCoverage(observationDate ? { observation_date: observationDate } : undefined),
    [observationDate],
  );
  const quality = useAsync(
    () => api.showcaseQuality(observationDate ? { day: observationDate } : undefined),
    [observationDate],
  );

  const dateOptions = useMemo(
    () =>
      (dates.data?.items ?? []).map((item) => ({
        label: dayjs(item.observation_date).format('DD.MM.YYYY'),
        value: item.observation_date,
      })),
    [dates.data],
  );

  const openRun = (runId: string) => navigate(`/breakdown/${runId}`);
  const summary = quality.data;
  const accuracy = summary?.stay_estimate_accuracy;

  return (
    <>
      <PageTitle
        title="Покрытие и качество"
        subtitle="Где есть цифра, где она на двух предложениях, а где наблюдения не было"
        extra={
          <Select
            value={observationDate}
            onChange={setObservationDate}
            options={dateOptions}
            loading={dates.loading}
            allowClear
            placeholder="последний срез"
            style={{ minWidth: 180 }}
          />
        }
      />

      <AsyncBlock loading={quality.loading} error={quality.error} minHeight={120}>
        {summary?.failed ? (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message={`Прогон ${summary.date} потерял ${percent(summary.missing_ratio)} сценариев`}
            description="Больше пяти процентов плана осталось без наблюдения. Причины — в отказах источников ниже; повторить сбор можно досбором дыр."
          />
        ) : null}

        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="В плане" value={summary?.planned ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Собрано" value={summary?.collected ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic
                title="Осталось дырами"
                value={summary?.missing ?? 0}
                valueStyle={summary?.missing ? { color: '#cf1322' } : undefined}
              />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic
                title="Длительность прогона"
                value={summary?.duration_minutes ?? 0}
                suffix="мин"
              />
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24} md={12}>
            <Card size="small" title="Средний размер выборки">
              <Paragraph type="secondary" style={{ fontSize: 12 }}>
                Медиана по четырем предложениям и по сорока выглядят одинаково, а
                доверия заслуживают разного.
              </Paragraph>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="Проезд"
                    value={summary?.avg_transport_offers ?? 0}
                    suffix="предл."
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="Проживание"
                    value={summary?.avg_accommodation_offers ?? 0}
                    suffix="предл."
                  />
                </Col>
              </Row>
              {summary?.partial_source_results ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Обрезанных выдач источника: {num(summary.partial_source_results)} — обход
                  прекращен по бюджету времени или уперся в потолок страниц.
                </Text>
              ) : null}
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card size="small" title="Точность оценки проживания">
              <Paragraph type="secondary" style={{ fontSize: 12 }}>
                Витрина считает проживание как «цена ночи × число ночей». Отели продают
                короткие брони дороже, поэтому оценка смещена — величину смещения
                измеряют контрольные пятидневные брони.
              </Paragraph>
              {accuracy?.pairs ? (
                <Statistic
                  title={`Расхождение оценки с реальной бронью (${accuracy.pairs} пар)`}
                  value={accuracy.median_deviation ?? 0}
                  formatter={(value) => percent(Number(value))}
                />
              ) : (
                <Text type="secondary">
                  Контрольных пар пока нет: нужны наблюдения и однодневной, и
                  пятидневной брони на одну дату.
                </Text>
              )}
            </Card>
          </Col>
        </Row>

        <Card size="small" title="Отказы источников" style={{ marginBottom: 16 }}>
          <Table
            size="small"
            rowKey={(row) => `${row.source_code}:${row.offer_type}:${row.outcome}`}
            pagination={false}
            dataSource={summary?.source_outcomes ?? []}
            columns={[
              { title: 'Источник', dataIndex: 'source_code' },
              { title: 'Компонента', dataIndex: 'offer_type' },
              {
                title: 'Исход',
                dataIndex: 'outcome',
                render: (value: string) => (
                  <Tag color={value === 'SUCCESS' ? 'green' : value === 'EMPTY' ? 'default' : 'orange'}>
                    {value}
                  </Tag>
                ),
              },
              { title: 'Обращений', dataIndex: 'count', align: 'right' },
              {
                title: 'Что это значит для цифр',
                dataIndex: 'meaning',
                render: (value: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {value}
                  </Text>
                ),
              },
            ]}
          />
        </Card>
      </AsyncBlock>

      <AsyncBlock loading={coverage.loading} error={coverage.error} minHeight={200}>
        <Card
          size="small"
          title="Проезд: маршруты по датам отправления"
          style={{ marginBottom: 16 }}
          extra={
            <Space size={12}>
              {Object.entries(coverage.data?.legend ?? {}).map(([state, title]) => (
                <Text key={state} type="secondary" style={{ fontSize: 11 }}>
                  <span style={{ color: CELL_COLOR[state as CoverageCell['state']] }}>
                    {CELL_GLYPH[state as CoverageCell['state']]}
                  </span>{' '}
                  {title}
                </Text>
              ))}
            </Space>
          }
        >
          <Table
            size="small"
            rowKey={(row) => `${row.origin_code}-${row.destination_code}-${row.transport_type}`}
            pagination={false}
            dataSource={coverage.data?.transport ?? []}
            scroll={{ x: 700 }}
            columns={[
              {
                title: 'Маршрут',
                key: 'route',
                width: 260,
                render: (_: unknown, row) => (
                  <Space size={6}>
                    <span>
                      {row.origin_name} → {row.destination_name}
                    </span>
                    <Tag>{row.transport_type === 'AVIA' ? 'самолет' : 'поезд'}</Tag>
                  </Space>
                ),
              },
              {
                title: 'Даты отправления',
                key: 'cells',
                render: (_: unknown, row) => <MatrixRow cells={row.cells} onOpen={openRun} />,
              },
            ]}
          />
        </Card>

        <Card size="small" title="Проживание: города по датам заезда">
          <Table
            size="small"
            rowKey={(row) => `${row.city_code}-${row.stars}`}
            pagination={false}
            dataSource={coverage.data?.accommodation ?? []}
            scroll={{ x: 700 }}
            columns={[
              {
                title: 'Город',
                key: 'city',
                width: 260,
                render: (_: unknown, row) => (
                  <Space size={6}>
                    <span>{row.city_name}</span>
                    <Tag>{row.stars}★</Tag>
                  </Space>
                ),
              },
              {
                title: 'Даты заезда',
                key: 'cells',
                render: (_: unknown, row) => <MatrixRow cells={row.cells} onOpen={openRun} />,
              },
            ]}
          />
        </Card>
      </AsyncBlock>
    </>
  );
}
