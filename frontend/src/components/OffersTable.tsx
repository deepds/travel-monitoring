/**
 * Полный список предложений, на которых построен расчет.
 *
 * Колонки зависят от типа: для поезда важен номер и класс вагона, для рейса —
 * перевозчик и багаж, для отеля — объект и условия. Показывать их одной
 * безликой таблицей «источник / цена» бессмысленно: именно детали позволяют
 * понять, почему цены различаются.
 */

import { Empty, Segmented, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import { api } from '@/api/client';
import type {
  AccommodationOfferDetail, FlightOfferDetail, Offer, RailOfferDetail,
} from '@/api/types';
import { AsyncBlock } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import {
  ACCOMMODATION_LABEL, BAGGAGE_LABEL, RAIL_CLASS_LABEL, dayTime, duration, money, num,
  sourceLabel, starsLabel,
} from '@/utils/format';

const { Text } = Typography;
const PAGE_SIZE = 25;

type TypeFilter = 'ALL' | 'RAIL' | 'FLIGHT' | 'ACCOMMODATION';

const rail = (row: Offer) => row.detail as RailOfferDetail | undefined;
const flight = (row: Offer) => row.detail as FlightOfferDetail | undefined;
const hotel = (row: Offer) => row.detail as AccommodationOfferDetail | undefined;

/** Учтено ли предложение в итоговой цифре — единственный статус, важный без разбора. */
function CountedTag({ row }: { row: Offer }) {
  if (row.exclusion_reason && row.exclusion_reason !== 'NONE') {
    return (
      <Tooltip title={row.exclusion_detail ?? row.exclusion_reason}>
        <Tag color="orange">не учтено</Tag>
      </Tooltip>
    );
  }
  if (row.is_outlier) return <Tag color="purple">выброс</Tag>;
  return <Tag color="success">учтено</Tag>;
}

const COMMON: ColumnsType<Offer> = [
  {
    title: 'Источник',
    dataIndex: 'source_code',
    width: 110,
    render: (value: string) => sourceLabel(value),
  },
  {
    title: 'Цена',
    dataIndex: 'total_price',
    align: 'right',
    width: 130,
    sorter: (a, b) => (a.total_price ?? 0) - (b.total_price ?? 0),
    render: (value: number | null) => <b>{money(value)}</b>,
  },
  { title: '', key: 'counted', width: 110, render: (_, row) => <CountedTag row={row} /> },
];

const RAIL_COLUMNS: ColumnsType<Offer> = [
  {
    title: 'Поезд',
    key: 'train',
    width: 130,
    render: (_, row) => {
      const detail = rail(row);
      if (!detail) return '—';
      const back = detail.inbound_train_number;
      return (
        <span className="tco-monospace">
          {detail.outbound_train_number ?? '—'}
          {back ? ` ↔ ${back}` : ''}
        </span>
      );
    },
  },
  {
    title: 'Вагон',
    key: 'car',
    width: 110,
    render: (_, row) => {
      const detail = rail(row);
      if (!detail?.car_type) return <Text type="secondary">{detail?.car_type_raw ?? '—'}</Text>;
      return <Tag>{RAIL_CLASS_LABEL[detail.car_type] ?? detail.car_type}</Tag>;
    },
  },
  {
    title: 'Отправление',
    key: 'departure',
    width: 130,
    render: (_, row) => dayTime(rail(row)?.outbound_departure_at),
  },
  {
    title: 'В пути',
    key: 'duration',
    width: 110,
    render: (_, row) => duration(rail(row)?.outbound_duration_minutes),
  },
  {
    title: 'Мест',
    key: 'places',
    align: 'right',
    width: 80,
    render: (_, row) => num(rail(row)?.available_places_outbound),
  },
  {
    title: 'За место',
    key: 'per_place',
    align: 'right',
    width: 120,
    render: (_, row) => money(rail(row)?.price_per_place_outbound),
  },
];

const FLIGHT_COLUMNS: ColumnsType<Offer> = [
  {
    title: 'Перевозчик',
    key: 'carrier',
    width: 160,
    render: (_, row) => flight(row)?.marketing_carriers?.join(', ') || '—',
  },
  {
    title: 'Рейсы',
    key: 'numbers',
    width: 130,
    render: (_, row) => (
      <span className="tco-monospace">{flight(row)?.flight_numbers?.join(', ') || '—'}</span>
    ),
  },
  {
    title: 'Вылет',
    key: 'departure',
    width: 130,
    render: (_, row) => dayTime(flight(row)?.outbound_departure_at),
  },
  {
    title: 'В пути',
    key: 'duration',
    width: 110,
    render: (_, row) => duration(flight(row)?.outbound_duration_minutes),
  },
  {
    title: 'Пересадки',
    key: 'stops',
    align: 'right',
    width: 100,
    render: (_, row) => {
      const stops = flight(row)?.outbound_stops;
      if (stops === null || stops === undefined) return '—';
      return stops === 0 ? 'прямой' : num(stops);
    },
  },
  {
    title: 'Багаж',
    key: 'baggage',
    width: 130,
    render: (_, row) => {
      const detail = flight(row);
      if (!detail) return '—';
      const label = BAGGAGE_LABEL[detail.baggage_type] ?? detail.baggage_type;
      return detail.baggage_type === 'UNKNOWN' ? <Tag color="warning">{label}</Tag> : label;
    },
  },
];

const ACCOMMODATION_COLUMNS: ColumnsType<Offer> = [
  {
    title: 'Объект',
    key: 'property',
    render: (_, row) => hotel(row)?.property_name || '—',
  },
  {
    title: 'Категория',
    key: 'stars',
    width: 140,
    render: (_, row) => {
      const detail = hotel(row);
      if (!detail) return '—';
      const type = ACCOMMODATION_LABEL[detail.accommodation_type] ?? detail.accommodation_type;
      return `${type} · ${starsLabel(detail.stars ? String(detail.stars) : detail.stars_status)}`;
    },
  },
  { title: 'Номер', key: 'room', render: (_, row) => hotel(row)?.room_name || '—' },
  {
    title: 'Ночей',
    key: 'nights',
    align: 'right',
    width: 80,
    render: (_, row) => num(hotel(row)?.nights),
  },
  {
    title: 'За ночь',
    dataIndex: 'price_per_night',
    align: 'right',
    width: 120,
    render: (value: number | null) => money(value),
  },
];

const TYPE_COLUMNS: Record<Exclude<TypeFilter, 'ALL'>, ColumnsType<Offer>> = {
  RAIL: RAIL_COLUMNS,
  FLIGHT: FLIGHT_COLUMNS,
  ACCOMMODATION: ACCOMMODATION_COLUMNS,
};

/** В смешанном списке детали не показать — колонки у типов разные. */
const MIXED_COLUMNS: ColumnsType<Offer> = [
  {
    title: 'Тип',
    dataIndex: 'offer_type',
    width: 130,
    render: (value: string) =>
      value === 'RAIL' ? 'Поезд' : value === 'FLIGHT' ? 'Перелет' : 'Проживание',
  },
  {
    title: 'Описание',
    key: 'summary',
    render: (_, row) => {
      if (row.offer_type === 'RAIL') {
        const detail = rail(row);
        return `Поезд ${detail?.outbound_train_number ?? '—'}, ${
          RAIL_CLASS_LABEL[detail?.car_type ?? ''] ?? detail?.car_type_raw ?? 'вагон не определен'
        }`;
      }
      if (row.offer_type === 'FLIGHT') {
        const detail = flight(row);
        return `${detail?.marketing_carriers?.join(', ') || 'рейс'} ${
          detail?.flight_numbers?.join(', ') ?? ''
        }`.trim();
      }
      return hotel(row)?.property_name || '—';
    },
  },
];

export function OffersTable({ runId }: { runId: string }) {
  const [kind, setKind] = useState<TypeFilter>('ALL');
  const [page, setPage] = useState(1);

  const offers = useAsync(
    () =>
      api.runOffers(runId, {
        page,
        page_size: PAGE_SIZE,
        offer_type: kind === 'ALL' ? undefined : kind,
      }),
    [runId, kind, page],
  );

  const data = offers.data;
  const columns =
    kind === 'ALL' ? [...MIXED_COLUMNS, ...COMMON] : [...TYPE_COLUMNS[kind], ...COMMON];

  return (
    <AsyncBlock loading={offers.loading} error={offers.error}>
      {data && data.offers_available === false ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            data.reason === 'PURGED'
              ? 'Предложения этого расчета удалены по сроку хранения. Сам расчет и его итоги сохранены.'
              : 'К расчету не привязан снимок рынка — отдельные предложения недоступны.'
          }
        />
      ) : (
        <>
          <Space style={{ marginBottom: 12 }}>
            <Segmented
              value={kind}
              onChange={(value) => {
                setKind(value as TypeFilter);
                setPage(1);
              }}
              options={[
                { value: 'ALL', label: 'Все' },
                { value: 'RAIL', label: 'Поезда' },
                { value: 'FLIGHT', label: 'Перелеты' },
                { value: 'ACCOMMODATION', label: 'Проживание' },
              ]}
            />
          </Space>

          <Table<Offer>
            size="small"
            rowKey="id"
            dataSource={data?.items ?? []}
            columns={columns}
            scroll={{ x: 900 }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: data?.meta?.total ?? 0,
              onChange: setPage,
              showSizeChanger: false,
              showTotal: (total) => `всего ${total}`,
            }}
          />
        </>
      )}
    </AsyncBlock>
  );
}
