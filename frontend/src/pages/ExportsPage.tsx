import { DownloadOutlined } from '@ant-design/icons';
import { Alert, App, Button, Card, Col, Form, Row, Select, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { ApiError, api, downloadFile } from '@/api/client';
import { AsyncBlock, PageTitle } from '@/components/common';
import { DEFAULT_FILTERS, DashboardFilterBar, toQuery } from '@/components/DashboardFilters';
import type { Filters } from '@/components/DashboardFilters';
import { useAsync, usePolling } from '@/hooks/useAsync';
import { JOB_STATUS_COLOR, JOB_STATUS_LABEL, dateTime, num } from '@/utils/format';

const { Text } = Typography;
const TERMINAL = ['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL'];

export default function ExportsPage() {
  const { message } = App.useApp();
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [dataset, setDataset] = useState('SCENARIO_RUNS');
  const [format, setFormat] = useState('CSV');
  const [submitting, setSubmitting] = useState(false);

  const jobs = useAsync(() => api.jobs({ job_type: 'EXPORT', page_size: 20 }), []);
  const pending = (jobs.data?.items ?? []).some((job) => !TERMINAL.includes(job.status));
  usePolling(() => jobs.reload(), 3000, pending);

  const submit = async () => {
    setSubmitting(true);
    try {
      await api.createExport({ dataset, format }, toQuery(filters));
      message.success('Экспорт поставлен в очередь');
      jobs.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось запустить экспорт');
    } finally {
      setSubmitting(false);
    }
  };

  const download = async (jobId: string) => {
    try {
      await downloadFile(api.downloadExportUrl(jobId), 'export');
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось скачать файл');
    }
  };

  return (
    <>
      <PageTitle
        title="Экспорт данных"
        subtitle="Выгрузка соответствует выбранному фильтру и включает поля качества, статусов и версии методики"
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Что попадает в выгрузку"
        description="Параметры сценария, дата расчета, горизонт бронирования, компонентные стоимости, итог, P25–P75, Quality Score, статус, источники, профиль и его версия."
      />

      <Card size="small" title="Параметры выгрузки" style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
          <Col xs={24} sm={10}>
            <Form.Item label="Набор данных" style={{ marginBottom: 0 }}>
              <Select
                value={dataset}
                onChange={setDataset}
                options={[
                  { value: 'SCENARIO_RUNS', label: 'Расчеты (ScenarioRun)' },
                  { value: 'OFFERS', label: 'Предложения' },
                  { value: 'SOURCE_METRICS', label: 'Метрики источников' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={6}>
            <Form.Item label="Формат" style={{ marginBottom: 0 }}>
              <Select
                value={format}
                onChange={setFormat}
                options={[
                  { value: 'CSV', label: 'CSV' },
                  { value: 'XLSX', label: 'Excel (XLSX)' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={8} style={{ display: 'flex', alignItems: 'flex-end' }}>
            <Button type="primary" icon={<DownloadOutlined />} onClick={submit} loading={submitting}>
              Сформировать
            </Button>
          </Col>
        </Row>
      </Card>

      <DashboardFilterBar value={filters} onChange={setFilters} />

      <Card size="small" title="История выгрузок">
        <AsyncBlock loading={jobs.loading && !jobs.data} error={jobs.error}>
          <Table
            size="small"
            rowKey="job_id"
            dataSource={jobs.data?.items ?? []}
            pagination={false}
            scroll={{ x: 900 }}
            columns={[
              {
                title: 'Набор',
                key: 'dataset',
                render: (_, row: any) => (
                  <Space>
                    <Tag>{row.params?.dataset}</Tag>
                    <Tag>{row.params?.format}</Tag>
                  </Space>
                ),
              },
              {
                title: 'Статус',
                dataIndex: 'status',
                render: (value: string) => (
                  <Tag color={JOB_STATUS_COLOR[value]}>{JOB_STATUS_LABEL[value] ?? value}</Tag>
                ),
              },
              {
                title: 'Строк',
                key: 'rows',
                align: 'right',
                render: (_, row: any) => num(row.result?.row_count),
              },
              {
                title: 'Размер',
                key: 'size',
                align: 'right',
                render: (_, row: any) =>
                  row.result?.size_bytes ? `${(row.result.size_bytes / 1024).toFixed(1)} КБ` : '—',
              },
              { title: 'Создан', dataIndex: 'created_at', render: (v: string) => dateTime(v) },
              {
                title: 'Инициатор',
                dataIndex: 'created_by',
                render: (v: string | null) => v ?? '—',
              },
              {
                title: '',
                key: 'download',
                align: 'right',
                render: (_, row: any) =>
                  row.status === 'SUCCESS' ? (
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => download(row.job_id)}
                    >
                      Скачать
                    </Button>
                  ) : row.error_message ? (
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {row.error_code}
                    </Text>
                  ) : null,
              },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
