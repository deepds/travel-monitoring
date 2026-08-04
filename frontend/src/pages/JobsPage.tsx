import { ReloadOutlined } from '@ant-design/icons';
import { App, Button, Card, Drawer, Select, Space, Switch, Table, Tag, Timeline, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import type { Job } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAuth } from '@/auth/AuthContext';
import { useAsync, usePolling } from '@/hooks/useAsync';
import {
  JOB_STATUS_COLOR, JOB_STATUS_LABEL, JOB_TYPE_LABEL, dateTime, labelOf,
} from '@/utils/format';

const { Text } = Typography;

export default function JobsPage() {
  const { message } = App.useApp();
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string>();
  const [jobType, setJobType] = useState<string>();
  const [activeOnly, setActiveOnly] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selected, setSelected] = useState<Job | null>(null);

  const query = useMemo(
    () => ({ page, page_size: 20, status, job_type: jobType, active_only: activeOnly }),
    [page, status, jobType, activeOnly],
  );
  const jobs = useAsync(() => api.jobs(query), [JSON.stringify(query)]);
  usePolling(() => jobs.reload(), 5000, autoRefresh);

  const events = useAsync(
    () => (selected ? api.jobEvents(selected.job_id) : Promise.resolve(null)),
    [selected?.job_id],
  );

  const retry = async (row: Job) => {
    try {
      await api.retryJob(row.job_id);
      message.success('Задача перезапущена');
      jobs.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось перезапустить');
    }
  };

  const cancel = async (row: Job) => {
    try {
      await api.cancelJob(row.job_id);
      message.success('Задача отменена');
      jobs.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось отменить');
    }
  };

  return (
    <>
      <PageTitle
        title="Фоновые задачи"
        subtitle="Длительные операции выполняются в фоне и остаются наблюдаемыми"
        extra={
          <Space>
            <Text type="secondary">автообновление</Text>
            <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
            <Button size="small" icon={<ReloadOutlined />} onClick={() => jobs.reload()} />
          </Space>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select allowClear placeholder="Статус" style={{ width: 160 }}
            options={Object.keys(JOB_STATUS_LABEL).map((v) => ({ value: v, label: JOB_STATUS_LABEL[v] }))}
            value={status} onChange={(v) => { setStatus(v); setPage(1); }} />
          <Select allowClear placeholder="Тип задачи" style={{ width: 230 }}
            options={Object.entries(JOB_TYPE_LABEL).map(([value, label]) => ({ value, label }))}
            value={jobType} onChange={(v) => { setJobType(v); setPage(1); }} />
          <Space size={4}>
            <Switch size="small" checked={activeOnly} onChange={(v) => { setActiveOnly(v); setPage(1); }} />
            <Text type="secondary">только незавершенные</Text>
          </Space>
        </Space>
      </Card>

      <Card size="small">
        <AsyncBlock loading={jobs.loading && !jobs.data} error={jobs.error}>
          <Table<Job>
            size="small"
            rowKey="job_id"
            dataSource={jobs.data?.items ?? []}
            scroll={{ x: 1100 }}
            onRow={(row) => ({ onClick: () => setSelected(row), className: 'tco-clickable' })}
            pagination={{
              current: page,
              pageSize: 20,
              total: jobs.data?.meta.total ?? 0,
              onChange: setPage,
              showTotal: (total) => `всего ${total}`,
            }}
            columns={[
              {
                title: 'Задача',
                dataIndex: 'job_id',
                fixed: 'left',
                width: 120,
                render: (value: string) => (
                  <span className="tco-monospace">{value.slice(0, 8)}…</span>
                ),
              },
              {
                title: 'Тип',
                dataIndex: 'job_type',
                render: (v: string) => <Tag>{labelOf(JOB_TYPE_LABEL, v)}</Tag>,
              },
              {
                title: 'Статус',
                dataIndex: 'status',
                render: (value: string) => (
                  <Tag color={JOB_STATUS_COLOR[value]}>{JOB_STATUS_LABEL[value] ?? value}</Tag>
                ),
              },
              {
                title: 'Прогресс',
                key: 'progress',
                render: (_, row) =>
                  row.progress?.total ? (
                    <Text type="secondary">
                      {row.progress.current}/{row.progress.total}
                    </Text>
                  ) : (
                    <Text type="secondary">{row.progress?.message ?? '—'}</Text>
                  ),
              },
              { title: 'Создана', dataIndex: 'created_at', render: (v: string) => dateTime(v) },
              { title: 'Завершена', dataIndex: 'finished_at', render: (v: string | null) => dateTime(v) },
              {
                title: 'Попытки',
                key: 'attempts',
                align: 'right',
                render: (_, row) => `${row.attempts}/${row.max_attempts}`,
              },
              { title: 'Инициатор', dataIndex: 'created_by', render: (v: string | null) => v ?? '—' },
              {
                title: 'Результат',
                key: 'result',
                render: (_, row) =>
                  row.scenario_run_id ? (
                    <Link to={`/runs/${row.scenario_run_id}`} onClick={(e) => e.stopPropagation()}>
                      расчет
                    </Link>
                  ) : row.error_code ? (
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {row.error_code}
                    </Text>
                  ) : (
                    '—'
                  ),
              },
              ...(can('ANALYST')
                ? [
                    {
                      title: 'Действия',
                      key: 'actions',
                      fixed: 'right' as const,
                      width: 150,
                      render: (_: unknown, row: Job) => (
                        <Space size="small" onClick={(e) => e.stopPropagation()}>
                          {['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL'].includes(row.status) &&
                          can('ADMIN') ? (
                            <Button size="small" type="link" onClick={() => retry(row)}>
                              Повторить
                            </Button>
                          ) : null}
                          {!['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL'].includes(row.status) ? (
                            <Button size="small" type="link" danger onClick={() => cancel(row)}>
                              Отменить
                            </Button>
                          ) : null}
                        </Space>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </AsyncBlock>
      </Card>

      <Drawer
        title="Хронология задачи"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={620}
      >
        {selected ? (
          <>
            <Space direction="vertical" style={{ marginBottom: 16 }}>
              <Text className="tco-monospace">{selected.job_id}</Text>
              <Space>
                <Tag>{labelOf(JOB_TYPE_LABEL, selected.job_type)}</Tag>
                <Tag color={JOB_STATUS_COLOR[selected.status]}>
                  {JOB_STATUS_LABEL[selected.status] ?? selected.status}
                </Tag>
              </Space>
              {selected.error_message ? (
                <Text type="danger">
                  {selected.error_code}: {selected.error_message}
                </Text>
              ) : null}
            </Space>

            <AsyncBlock loading={events.loading} error={events.error}>
              <Timeline
                items={(events.data?.items ?? []).map((event) => ({
                  color:
                    event.status === 'FAILED' ? 'red'
                      : event.status === 'SUCCESS' ? 'green'
                      : 'blue',
                  children: (
                    <div>
                      <Tag color={JOB_STATUS_COLOR[event.status]}>
                        {JOB_STATUS_LABEL[event.status] ?? event.status}
                      </Tag>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {dateTime(event.created_at)}
                      </Text>
                      <div>{event.message}</div>
                    </div>
                  ),
                }))}
              />
            </AsyncBlock>

            <details style={{ marginTop: 16 }}>
              <summary style={{ cursor: 'pointer' }}>Параметры и результат</summary>
              <pre className="tco-monospace" style={{ whiteSpace: 'pre-wrap' }}>
                {JSON.stringify({ params: selected.params, result: selected.result }, null, 2)}
              </pre>
            </details>
          </>
        ) : null}
      </Drawer>
    </>
  );
}
