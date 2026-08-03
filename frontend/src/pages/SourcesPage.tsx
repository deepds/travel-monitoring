import { HeartOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  Alert, App, Button, Card, Descriptions, Drawer, Form, Input, InputNumber,
  Modal, Progress, Space, Switch, Table, Tag, Tooltip, Typography,
} from 'antd';
import { useState } from 'react';

import { ApiError, api } from '@/api/client';
import type { Source } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAuth } from '@/auth/AuthContext';
import { useAsync } from '@/hooks/useAsync';
import { dateTime, num, percent, score } from '@/utils/format';

const { Text, Paragraph } = Typography;

const CONFIDENCE_COLOR: Record<string, string> = {
  HIGH: 'green',
  MEDIUM: 'gold',
  LOW: 'orange',
  UNTRUSTED: 'red',
};

export default function SourcesPage() {
  const { message } = App.useApp();
  const { can } = useAuth();
  const [selected, setSelected] = useState<Source | null>(null);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [form] = Form.useForm();

  const sources = useAsync(() => api.sources(), []);
  const overview = useAsync(() => api.sourcesOverview({ window_days: 30 }), []);

  const metrics = useAsync(
    () => (selected ? api.sourceMetrics(selected.code, { window_days: 30 }) : Promise.resolve(null)),
    [selected?.code],
  );
  const confidence = useAsync(
    () => (selected ? api.sourceConfidence(selected.code) : Promise.resolve(null)),
    [selected?.code],
  );

  const toggle = async (row: Source) => {
    try {
      if (row.is_enabled) await api.disableSource(row.code);
      else await api.enableSource(row.code);
      message.success(row.is_enabled ? 'Источник выключен' : 'Источник включен');
      sources.reload();
      overview.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось изменить источник');
    }
  };

  const healthCheck = async (row: Source) => {
    message.loading({ content: `Проверка ${row.code}...`, key: 'hc' });
    try {
      const result = await api.healthCheckSource(row.code);
      message.success({
        content: `Проверка завершена: ${JSON.stringify(result.check?.outcome ?? result.check ?? '')}`,
        key: 'hc',
      });
      sources.reload();
    } catch (exc) {
      message.error({
        content: exc instanceof ApiError ? exc.message : 'Проверка не удалась',
        key: 'hc',
      });
    }
  };

  const recalcConfidence = async (row: Source) => {
    try {
      const result = await api.recalcConfidence(row.code);
      message.success(`Доверие пересчитано: ${result.score.toFixed(1)} (${result.level})`);
      overview.reload();
      confidence.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось пересчитать');
    }
  };

  const submitOverride = async (values: { score: number; reason: string }) => {
    if (!selected) return;
    try {
      await api.overrideConfidence(selected.code, values);
      message.success('Ручное значение сохранено и записано в аудит');
      setOverrideOpen(false);
      form.resetFields();
      confidence.reload();
      overview.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось сохранить');
    }
  };

  const overviewByCode = new Map(
    (overview.data?.items ?? []).map((row) => [row.code, row]),
  );

  return (
    <>
      <PageTitle
        title="Качество источников"
        subtitle="Техническая стабильность, полнота данных и долгосрочная степень доверия"
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Source Confidence — не то же самое, что технический health"
        description="Это долгосрочная оценка доверия к источнику как поставщику данных. Она влияет на уверенность в результате, но не используется как непрозрачный вес цены."
      />

      <Card size="small">
        <AsyncBlock loading={sources.loading} error={sources.error}>
          <Table<Source>
            size="small"
            rowKey="id"
            dataSource={sources.data?.items ?? []}
            scroll={{ x: 1200 }}
            pagination={false}
            onRow={(row) => ({ onClick: () => setSelected(row), className: 'tco-clickable' })}
            columns={[
              {
                title: 'Источник',
                dataIndex: 'code',
                fixed: 'left',
                width: 200,
                render: (value: string, row) => (
                  <span>
                    <b>{value}</b>
                    {row.is_synthetic ? (
                      <Tag color="purple" style={{ marginLeft: 6 }}>
                        синтетический
                      </Tag>
                    ) : null}
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {row.name}
                    </Text>
                  </span>
                ),
              },
              {
                title: 'Категория',
                dataIndex: 'category',
                render: (value: string, row) => (
                  <span>
                    <Tag>{value === 'TRANSPORT' ? 'Транспорт' : 'Проживание'}</Tag>
                    <br />
                    {row.offer_types.map((t) => (
                      <Tag key={t} style={{ fontSize: 11 }}>
                        {t}
                      </Tag>
                    ))}
                  </span>
                ),
              },
              { title: 'Протокол', dataIndex: 'protocol', render: (v: string) => <Tag>{v}</Tag> },
              {
                title: 'Квалификация',
                dataIndex: 'qualification_status',
                render: (value: string) => (
                  <Tag
                    color={
                      value === 'APPROVED' ? 'success'
                        : value === 'CONDITIONAL' ? 'warning'
                        : value === 'REJECTED' ? 'error' : 'default'
                    }
                  >
                    {value}
                  </Tag>
                ),
              },
              {
                title: 'Успешность 30 дн.',
                key: 'success',
                align: 'right',
                render: (_, row) => {
                  const info = overviewByCode.get(row.code) as any;
                  const rate = info?.success_rate;
                  return rate == null ? '—' : (
                    <Tooltip title={`вызовов: ${num(info?.call_count)}`}>
                      <span>{percent(rate, 0)}</span>
                    </Tooltip>
                  );
                },
              },
              {
                title: 'Задержка',
                key: 'latency',
                align: 'right',
                render: (_, row) => {
                  const info = overviewByCode.get(row.code) as any;
                  return info?.avg_latency_ms == null ? '—' : `${Math.round(info.avg_latency_ms)} мс`;
                },
              },
              {
                title: 'Доверие',
                key: 'confidence',
                align: 'right',
                render: (_, row) => {
                  const info = overviewByCode.get(row.code) as any;
                  const level = info?.confidence_level;
                  return level ? (
                    <Tag color={CONFIDENCE_COLOR[level]}>
                      {score(info?.confidence_score)} · {level}
                    </Tag>
                  ) : (
                    <Text type="secondary">не рассчитано</Text>
                  );
                },
              },
              {
                title: 'Состояние',
                key: 'state',
                render: (_, row) => (
                  <Space direction="vertical" size={0}>
                    {row.is_usable ? <Tag color="success">пригоден</Tag> : <Tag>не используется</Tag>}
                    {row.circuit.is_open ? (
                      <Tooltip title={`до ${dateTime(row.circuit.open_until)}`}>
                        <Tag color="error">предохранитель разомкнут</Tag>
                      </Tooltip>
                    ) : null}
                  </Space>
                ),
              },
              ...(can('ADMIN')
                ? [
                    {
                      title: 'Действия',
                      key: 'actions',
                      fixed: 'right' as const,
                      width: 190,
                      render: (_: unknown, row: Source) => (
                        <Space size="small" onClick={(e) => e.stopPropagation()}>
                          <Switch size="small" checked={row.is_enabled} onChange={() => toggle(row)} />
                          <Tooltip title="Проверить доступность">
                            <Button size="small" type="text" icon={<HeartOutlined />}
                              onClick={() => healthCheck(row)} />
                          </Tooltip>
                          <Tooltip title="Пересчитать доверие">
                            <Button size="small" type="text" icon={<ReloadOutlined />}
                              onClick={() => recalcConfidence(row)} />
                          </Tooltip>
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
        title={selected ? `${selected.code} — ${selected.name}` : ''}
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={720}
        extra={
          can('ADMIN') && selected ? (
            <Button size="small" onClick={() => setOverrideOpen(true)}>
              Ручной override доверия
            </Button>
          ) : null
        }
      >
        {selected ? (
          <>
            <Descriptions size="small" column={1} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="Юридический статус">
                {selected.legal_status ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Право хранения">
                {selected.storage_allowed ? 'разрешено' : 'запрещено'} · HTML:{' '}
                {selected.html_storage_allowed ? 'разрешен' : 'запрещен'}
              </Descriptions.Item>
              <Descriptions.Item label="Горизонт">
                {selected.horizon.min_supported_date ?? '—'} — {selected.horizon.max_supported_date ?? '—'}
                {selected.horizon.booking_horizon_days
                  ? ` (${selected.horizon.booking_horizon_days} дн.)`
                  : ''}
              </Descriptions.Item>
              <Descriptions.Item label="Последний успех">
                {dateTime(selected.last_success_at)}
              </Descriptions.Item>
              <Descriptions.Item label="Последняя ошибка">
                {selected.last_error ? (
                  <Text type="danger" style={{ fontSize: 12 }}>
                    {dateTime(selected.last_failure_at)}: {selected.last_error}
                  </Text>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Версия коннектора">
                {(selected as any).connector_version ?? '—'}
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title="Технические метрики за 30 дней" style={{ marginBottom: 16 }}>
              <AsyncBlock loading={metrics.loading} error={metrics.error}>
                <Descriptions size="small" column={2} bordered>
                  {Object.entries(metrics.data?.summary ?? {}).map(([key, value]) => (
                    <Descriptions.Item key={key} label={key}>
                      {typeof value === 'number' ? value.toFixed(3).replace(/\.?0+$/, '') : String(value ?? '—')}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </AsyncBlock>
            </Card>

            <Card size="small" title="Source Confidence">
              <AsyncBlock loading={confidence.loading} error={confidence.error}>
                {confidence.data?.current ? (
                  <>
                    <Space style={{ marginBottom: 12 }}>
                      <Tag color={CONFIDENCE_COLOR[confidence.data.current.level]}>
                        {score(confidence.data.current.effective_score)} · {confidence.data.current.level}
                      </Tag>
                      <Text type="secondary">
                        формула {confidence.data.current.formula_version} · расчет{' '}
                        {confidence.data.current.calculation_date}
                      </Text>
                    </Space>
                    {confidence.data.current.manual_override != null ? (
                      <Alert
                        type="warning"
                        showIcon
                        style={{ marginBottom: 12 }}
                        message={`Задано вручную: ${confidence.data.current.manual_override}`}
                        description={`${confidence.data.current.override_reason} — ${confidence.data.current.approved_by}`}
                      />
                    ) : null}
                    <Paragraph type="secondary" style={{ fontSize: 12 }}>
                      Вклад факторов:
                    </Paragraph>
                    {Object.entries(confidence.data.current.factor_scores ?? {}).map(([key, value]) => (
                      <div key={key} style={{ marginBottom: 6 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                          <span>{key}</span>
                          <span>{((value as number) * 100).toFixed(0)} %</span>
                        </div>
                        <Progress percent={Math.round((value as number) * 100)} size="small" showInfo={false} />
                      </div>
                    ))}
                  </>
                ) : (
                  <Text type="secondary">Доверие еще не рассчитано</Text>
                )}
              </AsyncBlock>
            </Card>
          </>
        ) : null}
      </Drawer>

      <Modal
        title="Ручной override Source Confidence"
        open={overrideOpen}
        onCancel={() => setOverrideOpen(false)}
        onOk={() => form.submit()}
        okText="Сохранить"
        cancelText="Отмена"
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Действие аудируется"
          description="Исходное расчетное значение сохраняется. Указывайте содержательную причину."
        />
        <Form form={form} layout="vertical" onFinish={submitOverride}>
          <Form.Item name="score" label="Значение (0–100)" rules={[{ required: true }]}>
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="reason"
            label="Причина"
            rules={[{ required: true, min: 3, message: 'Укажите причину' }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
