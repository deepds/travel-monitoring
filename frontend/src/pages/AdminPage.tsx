import { InboxOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  Alert, App, Button, Card, Checkbox, Col, Descriptions, InputNumber, Row,
  Space, Statistic, Table, Tag, Typography, Upload,
} from 'antd';
import type { UploadFile } from 'antd';
import { useState } from 'react';

import { ApiError, api } from '@/api/client';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAsync } from '@/hooks/useAsync';
import {
  HEALTH_COMPONENT_LABEL, JOB_STATUS_COLOR, JOB_STATUS_LABEL, JOB_TYPE_LABEL,
  dateTime, labelOf, num,
} from '@/utils/format';

const { Text, Paragraph } = Typography;

export default function AdminPage() {
  const { message } = App.useApp();
  const [activate, setActivate] = useState(true);
  const [importing, setImporting] = useState(false);
  const [report, setReport] = useState<Record<string, any> | null>(null);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [limit, setLimit] = useState<number | null>(null);
  const [running, setRunning] = useState(false);

  const status = useAsync(() => api.monitoringStatus(), []);
  const health = useAsync(() => api.health(), []);
  const jobs = useAsync(() => api.monitoringJobs({ page_size: 15 }), []);

  const upload = async (file: UploadFile) => {
    setImporting(true);
    setReport(null);
    try {
      const result = await api.importScenarios(file as unknown as File, activate);
      setReport(result);
      const errors = result.error_count ?? 0;
      if (errors) {
        message.warning(`Импорт завершен с ошибками в ${errors} строках`);
      } else {
        message.success(`Импорт завершен: создано ${result.created}, обновлено ${result.updated}`);
      }
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Импорт не удался');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const runMonitoring = async () => {
    setRunning(true);
    try {
      const result = await api.runMonitoring({
        force_refresh: forceRefresh,
        limit: limit ?? undefined,
      });
      message.success(`Прогон запущен: сценариев ${result.eligible_scenario_count}`);
      jobs.reload();
      status.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось запустить прогон');
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <PageTitle title="Администрирование" subtitle="Каталог сценариев, мониторинг и состояние платформы" />

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Состояние мониторинга" extra={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => status.reload()} />
          }>
            <AsyncBlock loading={status.loading} error={status.error}>
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic title="Всего сценариев" value={status.data?.scenario_total ?? 0} />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="Активных"
                    value={status.data?.scenario_active ?? 0}
                    valueStyle={{
                      color: status.data?.meets_coverage_target ? '#3f8600' : '#cf1322',
                    }}
                  />
                </Col>
                <Col span={8}>
                  <Statistic title="Снимков в сутки" value={status.data?.snapshots_per_day ?? 0} />
                </Col>
              </Row>
              {!status.data?.meets_coverage_target ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 12 }}
                  message={`Целевое покрытие — не менее ${status.data?.kpi_target_scenarios ?? 100} активных сценариев мониторинга`}
                />
              ) : null}

              <Space direction="vertical" style={{ width: '100%', marginTop: 16 }}>
                <Space wrap>
                  <Checkbox checked={forceRefresh} onChange={(e) => setForceRefresh(e.target.checked)}>
                    Игнорировать идемпотентность окна
                  </Checkbox>
                  <InputNumber
                    placeholder="лимит сценариев"
                    min={1}
                    max={1000}
                    value={limit ?? undefined}
                    onChange={(value) => setLimit(value ?? null)}
                  />
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    onClick={runMonitoring}
                    loading={running}
                  >
                    Запустить прогон
                  </Button>
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Плановые снимки создаются автоматически каждые 6 часов. Без «игнорировать
                  идемпотентность» повторный запуск в том же окне не создаст дублирующие снимки.
                </Text>
              </Space>
            </AsyncBlock>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card size="small" title="Состояние подсистем" extra={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => health.reload()} />
          }>
            <AsyncBlock loading={health.loading} error={health.error}>
              <Descriptions size="small" column={1} bordered>
                {(health.data?.components ?? []).map((component) => (
                  <Descriptions.Item
                    key={component.name}
                    label={labelOf(HEALTH_COMPONENT_LABEL, component.name)}
                  >
                    <Space>
                      <Tag color={component.healthy ? 'success' : 'error'}>
                        {component.healthy ? 'исправно' : 'сбой'}
                      </Tag>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {component.detail}
                      </Text>
                    </Space>
                  </Descriptions.Item>
                ))}
              </Descriptions>
              {(health.data?.warnings ?? []).map((warning) => (
                <Alert key={warning} type="warning" showIcon style={{ marginTop: 8 }} message={warning} />
              ))}
            </AsyncBlock>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="Импорт каталога сценариев" style={{ marginBottom: 16 }}>
        <Paragraph type="secondary">
          Поддерживаются CSV и YAML. Ошибочные строки не срывают импорт целиком — они
          возвращаются в отчете с номером строки и причиной.
        </Paragraph>
        <Checkbox
          checked={activate}
          onChange={(e) => setActivate(e.target.checked)}
          style={{ marginBottom: 12 }}
        >
          Активировать существующие сценарии при повторном импорте
        </Checkbox>
        <Upload.Dragger
          accept=".csv,.yaml,.yml"
          maxCount={1}
          beforeUpload={upload}
          showUploadList={false}
          disabled={importing}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">Перетащите файл каталога или нажмите для выбора</p>
          <p className="ant-upload-hint">CSV или YAML, до 5 МБ, кодировка UTF-8</p>
        </Upload.Dragger>

        {report ? (
          <div style={{ marginTop: 16 }}>
            <Space wrap>
              <Tag color="success">создано: {num(report.created)}</Tag>
              <Tag color="blue">обновлено: {num(report.updated)}</Tag>
              <Tag>пропущено: {num(report.skipped)}</Tag>
              <Tag color={report.error_count ? 'error' : 'default'}>
                ошибок: {num(report.error_count)}
              </Tag>
            </Space>
            {report.errors?.length ? (
              <Table
                size="small"
                style={{ marginTop: 12 }}
                rowKey={(row: any) => `${row.row}-${row.code}`}
                dataSource={report.errors}
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: 'Строка', dataIndex: 'row', width: 90 },
                  { title: 'Код', dataIndex: 'code', width: 220 },
                  { title: 'Сообщение', dataIndex: 'message' },
                ]}
              />
            ) : null}
          </div>
        ) : null}
      </Card>

      <Card size="small" title="Последние задачи мониторинга">
        <AsyncBlock loading={jobs.loading} error={jobs.error}>
          <Table
            size="small"
            rowKey="job_id"
            dataSource={jobs.data?.items ?? []}
            pagination={false}
            scroll={{ x: 800 }}
            columns={[
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
                render: (_, row: any) =>
                  row.progress?.total ? `${row.progress.current}/${row.progress.total}` : '—',
              },
              { title: 'Создана', dataIndex: 'created_at', render: (v: string) => dateTime(v) },
              { title: 'Завершена', dataIndex: 'finished_at', render: (v: string | null) => dateTime(v) },
              {
                title: 'Ошибка',
                dataIndex: 'error_message',
                render: (value: string | null, row: any) =>
                  value ? (
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {row.error_code}: {value.slice(0, 80)}
                    </Text>
                  ) : (
                    '—'
                  ),
              },
            ]}
          />
        </AsyncBlock>
      </Card>
    </>
  );
}
