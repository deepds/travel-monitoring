/**
 * Сборка конкретной поездки из наблюдавшихся предложений.
 *
 * Расчет отвечает на вопрос «сколько стоит такая поездка вообще» — это медиана
 * рынка. Здесь вопрос другой: «сколько стоит вот эта поездка» — конкретный
 * поезд или рейс плюс конкретный отель. Цифра получается другая по устройству,
 * и путать их нельзя: медиана описывает рынок, а сумма выбранного — одну
 * покупку.
 *
 * Собирать можно только внутри одного снимка: транспорт и проживание должны
 * быть наблюдены одновременно, иначе итог складывается из цен разных моментов.
 * Поэтому конструктор живет на странице расчета, а не сценария.
 *
 * Выбор не сохраняется на сервере. Пользовательских сущностей в системе нет
 * вообще, и заводить их ради черновика поездки преждевременно: состав выбора
 * живет, пока открыта страница.
 */

import { Alert, Button, Card, Col, Row, Space, Tag, Tooltip, Typography } from 'antd';
import { useState } from 'react';

import type {
  AccommodationOfferDetail, FlightOfferDetail, Offer, RailOfferDetail,
} from '@/api/types';
import { OffersTable } from '@/components/OffersTable';
import { BAGGAGE_LABEL, RAIL_CLASS_LABEL, dayTime, money, sourceLabel, starsLabel } from '@/utils/format';

const { Text, Title } = Typography;

interface Selection {
  transport?: Offer;
  accommodation?: Offer;
}

/** Короткое человеческое описание предложения — чем оно отличается от соседних. */
function describe(offer: Offer): string {
  if (offer.offer_type === 'RAIL') {
    const rail = offer.detail as RailOfferDetail | undefined;
    const parts = [
      rail?.outbound_train_number ? `поезд ${rail.outbound_train_number}` : null,
      rail?.car_type ? RAIL_CLASS_LABEL[rail.car_type] ?? rail.car_type : null,
      rail?.outbound_departure_at ? dayTime(rail.outbound_departure_at) : null,
    ];
    return parts.filter(Boolean).join(' · ') || 'ЖД-предложение';
  }
  if (offer.offer_type === 'FLIGHT') {
    const flight = offer.detail as FlightOfferDetail | undefined;
    const parts = [
      (flight?.marketing_carriers ?? []).join(', ') || null,
      (flight?.flight_numbers ?? []).join(', ') || null,
      flight?.baggage_type ? BAGGAGE_LABEL[flight.baggage_type] ?? flight.baggage_type : null,
      flight?.outbound_departure_at ? dayTime(flight.outbound_departure_at) : null,
    ];
    return parts.filter(Boolean).join(' · ') || 'Авиапредложение';
  }
  const hotel = offer.detail as AccommodationOfferDetail | undefined;
  const parts = [
    hotel?.property_name || null,
    hotel?.stars ? starsLabel(String(hotel.stars)) : null,
    hotel?.room_name || null,
    hotel?.nights ? `${hotel.nights} ноч.` : null,
  ];
  return parts.filter(Boolean).join(' · ') || 'Проживание';
}

function Chosen({
  title,
  offer,
  onClear,
}: {
  title: string;
  offer?: Offer;
  onClear: () => void;
}) {
  if (!offer) {
    return (
      <Card size="small" style={{ height: '100%' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {title}
        </Text>
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">не выбрано</Text>
        </div>
      </Card>
    );
  }

  return (
    <Card size="small" style={{ height: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {title}
        </Text>
        <Button type="text" size="small" onClick={onClear}>
          убрать
        </Button>
      </Space>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>{money(offer.total_price)}</div>
      <Text style={{ fontSize: 13, display: 'block' }}>{describe(offer)}</Text>
      <Space size={8} style={{ marginTop: 8 }} wrap>
        <Tag>{sourceLabel(offer.source_code)}</Tag>
        {offer.exclusion_reason && offer.exclusion_reason !== 'NONE' ? (
          <Tooltip title={offer.exclusion_detail ?? offer.exclusion_reason}>
            <Tag color="orange">вне расчета</Tag>
          </Tooltip>
        ) : null}
        {offer.deeplink ? (
          <a href={offer.deeplink} target="_blank" rel="noreferrer noopener">
            Открыть у источника
          </a>
        ) : (
          // У РЖД ссылки на покупку нет ни у одного предложения: перевозчик
          // ее не отдает. Молчать об этом нельзя — иначе выглядит как сбой.
          <Tooltip title="Источник не дает прямой ссылки на покупку">
            <Text type="secondary" style={{ fontSize: 12 }}>
              ссылки нет
            </Text>
          </Tooltip>
        )}
      </Space>
    </Card>
  );
}

export function TripBuilder({ runId }: { runId: string }) {
  const [selection, setSelection] = useState<Selection>({});

  const pick = (offer: Offer) => {
    setSelection((current) =>
      offer.offer_type === 'ACCOMMODATION'
        ? { ...current, accommodation: offer }
        : { ...current, transport: offer },
    );
  };

  const transportPrice = selection.transport?.total_price ?? null;
  const stayPrice = selection.accommodation?.total_price ?? null;
  const total =
    transportPrice !== null && stayPrice !== null ? transportPrice + stayPrice : null;

  const selectedIds = [selection.transport?.id, selection.accommodation?.id].filter(
    (item): item is string => Boolean(item),
  );

  return (
    <>
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={8}>
          <Chosen
            title="Транспорт"
            offer={selection.transport}
            onClear={() => setSelection((current) => ({ ...current, transport: undefined }))}
          />
        </Col>
        <Col xs={24} md={8}>
          <Chosen
            title="Проживание"
            offer={selection.accommodation}
            onClear={() => setSelection((current) => ({ ...current, accommodation: undefined }))}
          />
        </Col>
        <Col xs={24} md={8}>
          <Card size="small" style={{ height: '100%' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Итого за поездку
            </Text>
            <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
              {total === null ? <Text type="secondary">—</Text> : money(total)}
            </div>
            {total === null ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {selection.transport || selection.accommodation
                  ? 'выберите вторую составляющую'
                  : 'выберите транспорт и проживание'}
              </Text>
            ) : (
              <Space size={4} style={{ marginTop: 4 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {money(transportPrice)} + {money(stayPrice)}
                </Text>
              </Space>
            )}
            {selectedIds.length ? (
              <div style={{ marginTop: 8 }}>
                <Button size="small" onClick={() => setSelection({})}>
                  Сбросить
                </Button>
              </div>
            ) : null}
          </Card>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Это не то же самое, что типовая стоимость"
        description="Здесь складываются цены двух конкретных предложений, наблюдавшихся в этом снимке. Типовая стоимость расчета — медиана рынка, и совпадать эти числа не обязаны. Наличие мест и цена в момент покупки могут отличаться."
      />

      <Title level={5} style={{ marginBottom: 8 }}>
        Из чего выбирать
      </Title>
      <Text type="secondary" style={{ fontSize: 13, display: 'block', marginBottom: 12 }}>
        Показаны все предложения снимка, включая не вошедшие в расчет: методика
        отсекает их от медианы, но поехать по ним можно.
      </Text>

      <OffersTable runId={runId} selectable selectedIds={selectedIds} onSelect={pick} />
    </>
  );
}
