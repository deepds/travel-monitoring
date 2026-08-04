/** Панель фильтров дашборда. Состав повторяет фильтры /api/v1/dashboard/*. */

import { Button, Card, Col, Row, Select } from 'antd';
import { useMemo } from 'react';

import { api } from '@/api/client';
import { useAsync } from '@/hooks/useAsync';

/**
 * Состав фильтров дашборда.
 *
 * Поля `include_synthetic` и `complete_only` остаются в запросе к API, но
 * органов управления для них нет: на текущем объеме данных они только сужали
 * и без того небольшую выборку. Значения берутся из `DEFAULT_FILTERS`.
 */
export interface Filters {
  origin?: string;
  destination?: string;
  /**
   * Фильтр по виду транспорта на дашборде не выводится: он спрятал бы половину
   * сравнения «Авиа против ЖД». Поле сохранено — им пользуются экраны
   * администратора и выгрузка.
   */
  transport_type?: string;
  include_synthetic: boolean;
  complete_only: boolean;
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
});
