import { Card, Select, Space, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '@/api/client';
import type { RunBrief } from '@/api/types';
import {
  AsyncBlock, LabelWithHint, PageTitle, PriceRange, RunStatusTag,
} from '@/components/common';
import { ExportButton } from '@/components/ExportButton';
import { useAsync } from '@/hooks/useAsync';
import { RUN_TYPE_LABEL, dateOnly, dateTime, labelOf, money } from '@/utils/format';
import { MIN_MAX_HINT, P25_P75_HINT } from '@/utils/hints';

const { Text } = Typography;

export default function RunsPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [origin, setOrigin] = useState<string>();
  const [destination, setDestination] = useState<string>();

  const cities = useAsync(() => api.cities(), []);
  // Список показывает все расчеты: отбор по полноте и по синтетике убран,
  // на текущем объеме данных он только сужал и без того небольшую выдачу.
  const query = useMemo(
    () => ({
      page,
      page_size: pageSize,
      origin,
      destination,
      include_synthetic: true,
    }),
    [page, pageSize, origin, destination],
  );

  const runs = useAsync(() => api.runs(query), [JSON.stringify(query)]);
  const cityOptions = (cities.data?.items ?? []).map((c) => ({ value: c.code, label: c.name }));

  return (
    <>
      <PageTitle
        title="Расчеты"
        subtitle="Неизменяемый исторический результат применения методики к снимку рынка"
        extra={
          <ExportButton
            dataset="SCENARIO_RUNS"
            filters={{ origin, destination, include_synthetic: true }}
            label="Выгрузить расчеты"
          />
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select allowClear placeholder="Откуда" style={{ width: 180 }} options={cityOptions}
            value={origin} onChange={(v) => { setOrigin(v); setPage(1); }} />
          <Select allowClear placeholder="Куда" style={{ width: 180 }} options={cityOptions}
            value={destination} onChange={(v) => { setDestination(v); setPage(1); }} />
        </Space>
      </Card>

      <Card size="small">
        <AsyncBlock loading={runs.loading} error={runs.error}>
          <Table<RunBrief>
            size="small"
            rowKey="id"
            dataSource={runs.data?.items ?? []}
            scroll={{ x: 1200 }}
            pagination={{
              current: page,
              pageSize,
              total: runs.data?.meta.total ?? 0,
              showSizeChanger: true,
              showTotal: (total) => `всего ${total}`,
              onChange: (nextPage, nextSize) => {
                setPage(nextPage);
                setPageSize(nextSize);
              },
            }}
            columns={[
              {
                title: 'Сценарий',
                dataIndex: 'scenario_code',
                fixed: 'left',
                width: 250,
                render: (value: string, row) => (
                  <span>
                    <Link to={`/runs/${row.id}`} className="tco-monospace">
                      {value}
                    </Link>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {row.scenario_name}
                    </Text>
                  </span>
                ),
              },
              {
                title: 'Наблюдение',
                dataIndex: 'observation_date',
                render: (value: string, row) => (
                  <span>
                    {dateOnly(value)}
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {dateTime(row.started_at)}
                    </Text>
                  </span>
                ),
              },
              {
                title: 'Тип',
                dataIndex: 'run_type',
                render: (v: string) => <Tag>{labelOf(RUN_TYPE_LABEL, v)}</Tag>,
              },
              { title: 'Статус', dataIndex: 'status', render: (v: any) => <RunStatusTag status={v} /> },
              {
                title: 'Итого',
                dataIndex: 'total_estimated_cost',
                align: 'right',
                render: (value: number | null) => <b>{money(value)}</b>,
              },
              {
                title: <LabelWithHint text="P25 – P75" hint={P25_P75_HINT} />,
                key: 'iqr',
                align: 'right',
                width: 190,
                render: (_, row) => <PriceRange low={row.total_p25} high={row.total_p75} />,
              },
              {
                title: <LabelWithHint text="min – max" hint={MIN_MAX_HINT} />,
                key: 'range',
                align: 'right',
                width: 190,
                render: (_, row) => <PriceRange low={row.total_min} high={row.total_max} />,
              },
              {
                title: 'На человека',
                dataIndex: 'price_per_person',
                align: 'right',
                render: (value: number | null) => money(value),
              },
              {
                title: 'Горизонт',
                dataIndex: 'lead_time_days',
                align: 'right',
                render: (value: number) => `${value} дн.`,
              },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
