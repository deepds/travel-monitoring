import { Card, Select, Space, Table, Tag } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '@/api/client';
import type { SnapshotBrief } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { SNAPSHOT_STATUS_LABEL, SNAPSHOT_TYPE_LABEL, dateTime, labelOf, num } from '@/utils/format';

const STATUS_COLOR: Record<string, string> = {
  COLLECTING: 'processing',
  COMPLETE: 'success',
  PARTIAL: 'warning',
  EMPTY: 'default',
  FAILED: 'error',
};

export default function SnapshotsPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [type, setType] = useState<string>();

  const query = useMemo(
    () => ({ page, page_size: pageSize, status, snapshot_type: type }),
    [page, pageSize, status, type],
  );
  const snapshots = useAsync(() => api.snapshots(query), [JSON.stringify(query)]);

  return (
    <>
      <PageTitle
        title="Снимки рынка"
        subtitle="Снимок фиксирует состояние рынка до применения расчетной методики и неизменяем после завершения"
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select allowClear placeholder="Статус" style={{ width: 170 }}
            options={Object.keys(STATUS_COLOR).map((v) => ({
              value: v,
              label: labelOf(SNAPSHOT_STATUS_LABEL, v),
            }))}
            value={status} onChange={(v) => { setStatus(v); setPage(1); }} />
          <Select allowClear placeholder="Тип снимка" style={{ width: 210 }}
            options={[
              { value: 'DAILY_MONITORING', label: 'Плановый мониторинг' },
              { value: 'ON_DEMAND', label: 'По запросу' },
              { value: 'MANUAL_RETRY', label: 'Ручной повтор' },
              { value: 'METHODOLOGY_REPLAY', label: 'Пересчет методики' },
            ]}
            value={type} onChange={(v) => { setType(v); setPage(1); }} />
        </Space>
      </Card>

      <Card size="small">
        <AsyncBlock loading={snapshots.loading} error={snapshots.error}>
          <Table<SnapshotBrief>
            size="small"
            rowKey="id"
            dataSource={snapshots.data?.items ?? []}
            scroll={{ x: 1100 }}
            pagination={{
              current: page,
              pageSize,
              total: snapshots.data?.meta.total ?? 0,
              showSizeChanger: true,
              showTotal: (total) => `всего ${total}`,
              onChange: (nextPage, nextSize) => {
                setPage(nextPage);
                setPageSize(nextSize);
              },
            }}
            columns={[
              {
                title: 'Снимок',
                dataIndex: 'id',
                fixed: 'left',
                width: 130,
                render: (value: string) => (
                  <Link to={`/snapshots/${value}`} className="tco-monospace">
                    {value.slice(0, 8)}…
                  </Link>
                ),
              },
              // Ширина не задана намеренно: свободное место таблицы уходит
              // сюда, а не копится между столбцами с короткими значениями.
              // Коды сценариев длинные, и переносить их есть куда.
              { title: 'Сценарий', dataIndex: 'scenario_code', render: (v: string) => <span className="tco-monospace">{v}</span> },
              {
                title: 'Наблюдение',
                dataIndex: 'observed_at',
                width: 215,
                render: (value: string, row) => (
                  // Дата и окно наблюдения — одна величина, и переносить плашку
                  // под дату нельзя: строка таблицы вырастает вдвое, а плашка
                  // начинает читаться как отдельное свойство снимка.
                  <span style={{ whiteSpace: 'nowrap' }}>
                    {dateTime(value)}
                    {row.observation_slot != null ? <Tag style={{ marginLeft: 6 }}>окно {row.observation_slot}</Tag> : null}
                  </span>
                ),
              },
              {
                title: 'Тип',
                dataIndex: 'snapshot_type',
                width: 180,
                render: (v: string) => <Tag>{labelOf(SNAPSHOT_TYPE_LABEL, v)}</Tag>,
              },
              {
                title: 'Статус',
                dataIndex: 'status',
                width: 120,
                render: (value: string) => (
                  <Tag color={STATUS_COLOR[value]}>{labelOf(SNAPSHOT_STATUS_LABEL, value)}</Tag>
                ),
              },
              {
                title: 'Транспорт',
                dataIndex: 'transport_offer_count',
                align: 'right',
                width: 110,
                render: (v: number) => num(v),
              },
              {
                title: 'Проживание',
                dataIndex: 'accommodation_offer_count',
                align: 'right',
                width: 120,
                render: (v: number) => num(v),
              },
              {
                title: 'Валидных',
                dataIndex: 'valid_offer_count',
                align: 'right',
                width: 110,
                render: (v: number) => num(v),
              },
              {
                title: 'Источники',
                dataIndex: 'source_codes',
                width: 170,
                render: (value: string[]) => value.map((code) => <Tag key={code}>{code}</Tag>),
              },
              {
                title: '',
                key: 'flags',
                render: (_, row) =>
                  row.offers_available ? null : <Tag color="default">предложения очищены</Tag>,
              },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
