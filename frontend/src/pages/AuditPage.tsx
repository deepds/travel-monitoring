import { Card, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';

import { api } from '@/api/client';
import type { AuditEvent } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import { dateTime } from '@/utils/format';

const { Text } = Typography;

const ACTIONS = [
  'LOGIN', 'SCENARIO_CREATE', 'SCENARIO_UPDATE', 'SCENARIO_ACTIVATE', 'SCENARIO_DEACTIVATE',
  'SCENARIO_DELETE', 'SCENARIO_IMPORT', 'PROFILE_CREATE', 'PROFILE_ACTIVATE', 'PROFILE_ARCHIVE',
  'PROFILE_CLONE', 'SOURCE_UPDATE', 'SOURCE_ENABLE', 'SOURCE_DISABLE', 'SOURCE_HEALTH_CHECK',
  'SOURCE_CONFIDENCE_OVERRIDE', 'MONITORING_RUN', 'SNAPSHOT_RECALCULATE', 'JOB_RETRY',
  'JOB_CANCEL', 'EXPORT', 'CACHE_PURGE', 'RETENTION_CLEANUP',
];

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
            options={ACTIONS.map((value) => ({ value, label: value }))}
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
                  <Tag color={CRITICAL.has(value) ? 'red' : 'default'}>{value}</Tag>
                ),
              },
              {
                title: 'Пользователь',
                key: 'actor',
                render: (_, row) => (
                  <span>
                    {row.actor.username ?? '—'}
                    {row.actor.role ? (
                      <Tag style={{ marginLeft: 6 }}>{row.actor.role}</Tag>
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
                      {row.object_type}
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
