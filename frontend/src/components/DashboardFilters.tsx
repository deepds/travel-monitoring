/** Панель фильтров дашборда. Состав повторяет фильтры /api/v1/dashboard/*. */

import { Button, Card, Checkbox, Col, Row, Select, Slider } from 'antd';
import { useMemo } from 'react';

import { api } from '@/api/client';
import { useAsync } from '@/hooks/useAsync';

export interface Filters {
  origin?: string;
  destination?: string;
  transport_type?: string;
  include_synthetic: boolean;
  complete_only: boolean;
  min_quality_score?: number;
}

export const DEFAULT_FILTERS: Filters = {
  include_synthetic: false,
  complete_only: false,
};

interface Props {
  value: Filters;
  onChange: (next: Filters) => void;
}

export function DashboardFilterBar({ value, onChange }: Props) {
  const { data: cities } = useAsync(() => api.cities(), []);

  const cityOptions = useMemo(
    () => (cities?.items ?? []).map((city) => ({ value: city.code, label: city.name })),
    [cities],
  );

  const set = <K extends keyof Filters>(key: K, next: Filters[K]) =>
    onChange({ ...value, [key]: next });

  return (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Row gutter={[12, 12]} align="middle">
        <Col xs={24} sm={12} md={5}>
          <Select
            allowClear
            placeholder="Откуда"
            style={{ width: '100%' }}
            options={cityOptions}
            value={value.origin}
            onChange={(next) => set('origin', next)}
          />
        </Col>
        <Col xs={24} sm={12} md={5}>
          <Select
            allowClear
            placeholder="Куда"
            style={{ width: '100%' }}
            options={cityOptions}
            value={value.destination}
            onChange={(next) => set('destination', next)}
          />
        </Col>
        <Col xs={24} sm={12} md={4}>
          <Select
            allowClear
            placeholder="Транспорт"
            style={{ width: '100%' }}
            options={[
              { value: 'AVIA', label: 'Авиа' },
              { value: 'RAIL', label: 'ЖД' },
            ]}
            value={value.transport_type}
            onChange={(next) => set('transport_type', next)}
          />
        </Col>
        <Col xs={24} sm={12} md={5}>
          <div style={{ fontSize: 12, color: '#8c8c8c' }}>
            Мин. Quality Score: {value.min_quality_score ?? 0}
          </div>
          <Slider
            min={0}
            max={100}
            step={5}
            value={value.min_quality_score ?? 0}
            onChange={(next) => set('min_quality_score', next === 0 ? undefined : next)}
          />
        </Col>
        <Col xs={24} md={5}>
          <Checkbox
            checked={value.complete_only}
            onChange={(event) => set('complete_only', event.target.checked)}
          >
            Только полные расчеты
          </Checkbox>
          <br />
          <Checkbox
            checked={value.include_synthetic}
            onChange={(event) => set('include_synthetic', event.target.checked)}
          >
            С синтетическими
          </Checkbox>
        </Col>
      </Row>
      <div style={{ marginTop: 8 }}>
        <Button size="small" onClick={() => onChange(DEFAULT_FILTERS)}>
          Сбросить фильтры
        </Button>
      </div>
    </Card>
  );
}

/** Приводит фильтры к query-параметрам API. */
export const toQuery = (filters: Filters): Record<string, string | number | boolean | undefined> => ({
  origin: filters.origin,
  destination: filters.destination,
  transport_type: filters.transport_type,
  include_synthetic: filters.include_synthetic,
  complete_only: filters.complete_only,
  min_quality_score: filters.min_quality_score,
});
