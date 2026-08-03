import { Card, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '@/api/client';
import type { RunBrief } from '@/api/types';
import { AsyncBlock, ConfidenceTag, PageTitle, RunStatusTag, SyntheticTag } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { dateOnly, dateTime, money, score } from '@/utils/format';

const { Text } = Typography;

export default function RunsPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [runType, setRunType] = useState<string>();
  const [confidence, setConfidence] = useState<string>();
  const [origin, setOrigin] = useState<string>();
  const [destination, setDestination] = useState<string>();
  const [completeOnly, setCompleteOnly] = useState(false);
  const [includeSynthetic, setIncludeSynthetic] = useState(true);

  const cities = useAsync(() => api.cities(), []);
  const query = useMemo(
    () => ({
      page,
      page_size: pageSize,
      status,
      run_type: runType,
      confidence_level: confidence,
      origin,
      destination,
      complete_only: completeOnly,
      include_synthetic: includeSynthetic,
    }),
    [page, pageSize, status, runType, confidence, origin, destination, completeOnly, includeSynthetic],
  );

  const runs = useAsync(() => api.runs(query), [JSON.stringify(query)]);
  const cityOptions = (cities.data?.items ?? []).map((c) => ({ value: c.code, label: c.name }));

  return (
    <>
      <PageTitle
        title="Расчеты"
        subtitle="ScenarioRun — неизменяемый исторический результат применения методики"
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select allowClear placeholder="Статус" style={{ width: 170 }}
            options={[
              { value: 'SUCCESS', label: 'Успешно' },
              { value: 'PARTIAL_SUCCESS', label: 'Частично' },
              { value: 'FAILED', label: 'Ошибка' },
              { value: 'UNSUPPORTED', label: 'Не поддерживается' },
              { value: 'NO_DATA', label: 'Нет данных' },
            ]}
            value={status} onChange={(v) => { setStatus(v); setPage(1); }} />
          <Select allowClear placeholder="Тип запуска" style={{ width: 150 }}
            options={[
              { value: 'MONITORING', label: 'Мониторинг' },
              { value: 'ON_DEMAND', label: 'По запросу' },
              { value: 'REPLAY', label: 'Пересчет' },
              { value: 'MANUAL', label: 'Ручной' },
            ]}
            value={runType} onChange={(v) => { setRunType(v); setPage(1); }} />
          <Select allowClear placeholder="Уверенность" style={{ width: 160 }}
            options={[
              { value: 'HIGH', label: 'Высокая' },
              { value: 'MEDIUM', label: 'Средняя' },
              { value: 'LOW', label: 'Низкая' },
              { value: 'INSUFFICIENT', label: 'Недостаточно' },
            ]}
            value={confidence} onChange={(v) => { setConfidence(v); setPage(1); }} />
          <Select allowClear placeholder="Откуда" style={{ width: 150 }} options={cityOptions}
            value={origin} onChange={(v) => { setOrigin(v); setPage(1); }} />
          <Select allowClear placeholder="Куда" style={{ width: 150 }} options={cityOptions}
            value={destination} onChange={(v) => { setDestination(v); setPage(1); }} />
          <Space size={4}>
            <Switch size="small" checked={completeOnly} onChange={setCompleteOnly} />
            <Text type="secondary">только полные</Text>
          </Space>
          <Space size={4}>
            <Switch size="small" checked={includeSynthetic} onChange={setIncludeSynthetic} />
            <Text type="secondary">с синтетическими</Text>
          </Space>
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
              { title: 'Тип', dataIndex: 'run_type', render: (v: string) => <Tag>{v}</Tag> },
              { title: 'Статус', dataIndex: 'status', render: (v: any) => <RunStatusTag status={v} /> },
              {
                title: 'Итого',
                dataIndex: 'total_estimated_cost',
                align: 'right',
                render: (value: number | null) => <b>{money(value)}</b>,
              },
              {
                title: 'На человека',
                dataIndex: 'price_per_person',
                align: 'right',
                render: (value: number | null) => money(value),
              },
              {
                title: 'Quality',
                dataIndex: 'quality_score',
                align: 'right',
                render: (value: number | null) => score(value),
              },
              {
                title: 'Уверенность',
                dataIndex: 'confidence_level',
                render: (value: any) => <ConfidenceTag level={value} />,
              },
              {
                title: 'Горизонт',
                dataIndex: 'lead_time_days',
                align: 'right',
                render: (value: number) => `${value} дн.`,
              },
              {
                title: '',
                dataIndex: 'contains_synthetic_data',
                render: (value: boolean) => <SyntheticTag visible={value} />,
              },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
