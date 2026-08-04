import { Card, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';

import { api } from '@/api/client';
import type { AuditEvent } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { AUDIT_ACTION_LABEL, USER_ROLE_LABEL, dateTime, labelOf } from '@/utils/format';

const { Text } = Typography;

/** Типы объектов, к которым относятся записи журнала. */
const OBJECT_TYPE_LABEL: Record<string, string> = {
  TravelScenario: 'Сценарий',
  MarketSnapshot: 'Снимок рынка',
  CalculationProfile: 'Профиль расчета',
  Source: 'Источник',
  SourceConfidence: 'Доверие к источнику',
  MonitoringBatch: 'Пакет мониторинга',
  Job: 'Задача',
  User: 'Пользователь',
};

const CRITICAL = new Set([
  'SOURCE_CONFIDENCE_OVERRIDE', 'PROFILE_ACTIVATE', 'PROFILE_ARCHIVE',
  'SCENARIO_DELETE', 'CACHE_PURGE', 'SOURCE_DISABLE',
]);

export default function AuditPage() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState<string>();
  const [actor, setActor] = useState<string>();

  const query = useMemo(
    () => ({ page, page_size: 25, action, actor: actor || undefined }),
    [page, action, actor],
  );
  const events = useAsync(() => api.auditEvents(query), [JSON.stringify(query)]);

  return (
    <>
      <PageTitle
        title="Журнал аудита"
        subtitle="Административные и значимые действия: кто, что и когда изменил"
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            allowClear
            showSearch
            placeholder="Действие"
            style={{ width: 280 }}
            options={Object.entries(AUDIT_ACTION_LABEL).map(([value, label]) => ({
              value,
              label,
            }))}
            value={action}
            onChange={(value) => { setAction(value); setPage(1); }}
          />
          <Input.Search
            allowClear
            placeholder="Пользователь"
            style={{ width: 220 }}
            onSearch={(value) => { setActor(value); setPage(1); }}
          />
        </Space>
      </Card>

      <Card size="small">
        <AsyncBlock loading={events.loading} error={events.error}>
          <Table<AuditEvent>
            size="small"
            rowKey="id"
            dataSource={events.data?.items ?? []}
            scroll={{ x: 1000 }}
            expandable={{
              expandedRowRender: (row) => (
                <pre className="tco-monospace" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                  {JSON.stringify(row.payload, null, 2)}
                </pre>
              ),
              rowExpandable: (row) => Object.keys(row.payload ?? {}).length > 0,
            }}
            pagination={{
              current: page,
              pageSize: 25,
              total: events.data?.meta.total ?? 0,
              onChange: setPage,
              showTotal: (total) => `всего ${total}`,
            }}
            columns={[
              {
                title: 'Время',
                dataIndex: 'created_at',
                width: 160,
                render: (value: string) => dateTime(value),
              },
              {
                title: 'Действие',
                dataIndex: 'action',
                render: (value: string) => (
                  <Tag color={CRITICAL.has(value) ? 'red' : 'default'}>
                    {labelOf(AUDIT_ACTION_LABEL, value)}
                  </Tag>
                ),
              },
              {
                title: 'Пользователь',
                key: 'actor',
                render: (_, row) => (
                  <span>
                    {row.actor.username ?? '—'}
                    {row.actor.role ? (
                      <Tag style={{ marginLeft: 6 }}>
                        {labelOf(USER_ROLE_LABEL, row.actor.role)}
                      </Tag>
                    ) : null}
                  </span>
                ),
              },
              {
                title: 'Объект',
                key: 'object',
                render: (_, row) =>
                  row.object_type ? (
                    <span>
                      {labelOf(OBJECT_TYPE_LABEL, row.object_type)}
                      <br />
                      <Text type="secondary" className="tco-monospace" style={{ fontSize: 11 }}>
                        {row.object_id?.slice(0, 18)}
                      </Text>
                    </span>
                  ) : (
                    '—'
                  ),
              },
              { title: 'Описание', dataIndex: 'summary' },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
