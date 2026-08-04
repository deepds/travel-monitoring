import { ReloadOutlined } from '@ant-design/icons';
import {
  Alert, App, Button, Card, Col, Descriptions, Modal, Row, Select, Space,
  Table, Tabs, Tag, Typography,
} from 'antd';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import type { Offer, RunBrief, SnapshotSourceRow } from '@/api/types';
import {
  AsyncBlock, ConfidenceTag, LabelWithHint, PageTitle, RunStatusTag,
} from '@/components/common';
import { useAuth } from '@/auth/AuthContext';
import { useAsync } from '@/hooks/useAsync';
import {
  CLASSIFICATION_LABEL, EXCLUSION_LABEL, OFFER_TYPE_LABEL, OUTCOME_COLOR, OUTCOME_LABEL,
  PROFILE_STATUS_LABEL, RUN_TYPE_LABEL, SNAPSHOT_STATUS_LABEL, SNAPSHOT_TYPE_LABEL,
  VALIDITY_LABEL, dateTime, labelOf, money, num, score,
} from '@/utils/format';
import { QUALITY_SCORE_SHORT_HINT } from '@/utils/hints';

const { Text } = Typography;

export default function SnapshotDetailPage() {
  const { id = '' } = useParams();
  const { message } = App.useApp();
  const { can } = useAuth();
  const [recalcOpen, setRecalcOpen] = useState(false);
  const [profileId, setProfileId] = useState<string>();

  const snapshot = useAsync(() => api.snapshot(id), [id]);
  const sources = useAsync(() => api.snapshotSources(id), [id]);
  const profiles = useAsync(() => api.profiles(), []);
  const [offerPage, setOfferPage] = useState(1);
  const offers = useAsync(
    () => api.snapshotOffers(id, { page: offerPage, page_size: 25 }),
    [id, offerPage],
  );
  const artifacts = useAsync(() => api.snapshotArtifacts(id), [id]);

  const data = snapshot.data;

  const recalculate = async () => {
    try {
      await api.recalculateSnapshot(id, { profile_id: profileId });
      message.success('Пересчет запущен. Исходный снимок не изменен.');
      setRecalcOpen(false);
      snapshot.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось запустить пересчет');
    }
  };

  return (
    <>
      <PageTitle
        title="Снимок рынка"
        subtitle={data?.scenario_code ?? undefined}
        extra={
          can('ANALYST') && data?.offers_available && data?.is_finalized ? (
            <Button icon={<ReloadOutlined />} onClick={() => setRecalcOpen(true)}>
              Пересчитать другим профилем
            </Button>
          ) : null
        }
      />

      <AsyncBlock loading={snapshot.loading} error={snapshot.error}>
        {!data?.offers_available ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="Предложения снимка удалены политикой хранения"
            description="Метаданные и итоги по источникам сохранены, но пересчет по этому снимку невозможен."
          />
        ) : null}

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={14}>
            <Card size="small" title="Метаданные">
              <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered>
                <Descriptions.Item label="Статус">
                  <Tag color={data?.status === 'COMPLETE' ? 'success' : 'warning'}>
                    {labelOf(SNAPSHOT_STATUS_LABEL, data?.status)}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Тип">
                  <Tag>{labelOf(SNAPSHOT_TYPE_LABEL, data?.snapshot_type)}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Момент наблюдения">
                  {dateTime(data?.observed_at)}
                </Descriptions.Item>
                <Descriptions.Item label="Внутрисуточное окно">
                  {data?.observation_slot ?? '—'}
                </Descriptions.Item>
                <Descriptions.Item label="Запрошен">{dateTime(data?.requested_at)}</Descriptions.Item>
                <Descriptions.Item label="Завершен">{dateTime(data?.completed_at)}</Descriptions.Item>
                <Descriptions.Item label="Предложений транспорт">
                  {num(data?.transport_offer_count)}
                </Descriptions.Item>
                <Descriptions.Item label="Предложений проживание">
                  {num(data?.accommodation_offer_count)}
                </Descriptions.Item>
                <Descriptions.Item label="Валидных">{num(data?.valid_offer_count)}</Descriptions.Item>
                <Descriptions.Item label="Невалидных">{num(data?.invalid_offer_count)}</Descriptions.Item>
                <Descriptions.Item label="Дубликатов">{num(data?.duplicate_offer_count)}</Descriptions.Item>
                <Descriptions.Item label="Выбросов">{num(data?.outlier_offer_count)}</Descriptions.Item>
                <Descriptions.Item label="Исходных ответов">
                  {num(data?.raw_response_count)}
                </Descriptions.Item>
                <Descriptions.Item label="Снимков страниц">
                  {num(data?.html_snapshot_count)}
                </Descriptions.Item>
                <Descriptions.Item label="Версия нормализации">
                  {data?.normalization_version}
                </Descriptions.Item>
                <Descriptions.Item label="Создан">
                  {data?.created_by ?? '—'} · {dateTime(data?.created_at)}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card
              size="small"
              title={`Расчеты по снимку (${data?.run_count ?? 0})`}
              extra={<Text type="secondary">один снимок — несколько методик</Text>}
            >
              <Table<RunBrief>
                size="small"
                rowKey="id"
                dataSource={data?.runs ?? []}
                pagination={false}
                columns={[
                  {
                    title: 'Расчет',
                    dataIndex: 'id',
                    render: (value: string) => (
                      <Link to={`/runs/${value}`} className="tco-monospace">
                        {value.slice(0, 8)}…
                      </Link>
                    ),
                  },
                  {
                    title: 'Тип',
                    dataIndex: 'run_type',
                    render: (v: string) => <Tag>{labelOf(RUN_TYPE_LABEL, v)}</Tag>,
                  },
                  {
                    title: 'Статус',
                    dataIndex: 'status',
                    render: (v: any) => <RunStatusTag status={v} />,
                  },
                  {
                    title: 'Итого',
                    dataIndex: 'total_estimated_cost',
                    align: 'right',
                    render: (v: number | null) => money(v),
                  },
                  {
                    title: <LabelWithHint text="Качество" hint={QUALITY_SCORE_SHORT_HINT} />,
                    dataIndex: 'quality_score',
                    align: 'right',
                    render: (v: number | null) => score(v),
                  },
                  {
                    title: 'Уверенность',
                    dataIndex: 'confidence_level',
                    render: (v: any) => <ConfidenceTag level={v} />,
                  },
                ]}
              />
            </Card>
          </Col>
        </Row>

        <Card size="small" style={{ marginTop: 16 }}>
          <Tabs
            items={[
              {
                key: 'sources',
                label: 'Итоги по источникам',
                children: (
                  <AsyncBlock loading={sources.loading} error={sources.error}>
                    <Table<SnapshotSourceRow>
                      size="small"
                      rowKey={(row) => `${row.source_code}-${row.offer_type}`}
                      dataSource={sources.data?.items ?? []}
                      pagination={false}
                      scroll={{ x: 1000 }}
                      columns={[
                        { title: 'Источник', dataIndex: 'source_code' },
                        {
                          title: 'Тип',
                          dataIndex: 'offer_type',
                          render: (v: string) => <Tag>{labelOf(OFFER_TYPE_LABEL, v)}</Tag>,
                        },
                        {
                          title: 'Итог',
                          dataIndex: 'outcome',
                          render: (value: string) => (
                            <Tag color={OUTCOME_COLOR[value] ?? 'default'}>
                              {labelOf(OUTCOME_LABEL, value)}
                            </Tag>
                          ),
                        },
                        {
                          title: 'Задержка',
                          dataIndex: 'latency_ms',
                          align: 'right',
                          render: (v: number | null) => (v == null ? '—' : `${v} мс`),
                        },
                        { title: 'Попыток', dataIndex: 'attempts', align: 'right' },
                        { title: 'Получено', dataIndex: 'raw_offer_count', align: 'right' },
                        { title: 'Валидных', dataIndex: 'valid_offer_count', align: 'right' },
                        { title: 'Невалидных', dataIndex: 'invalid_offer_count', align: 'right' },
                        { title: 'Неклассиф.', dataIndex: 'unclassified_offer_count', align: 'right' },
                        {
                          title: 'Ошибка',
                          dataIndex: 'error_message',
                          render: (value: string | null, row) =>
                            value ? (
                              <Text type="danger" style={{ fontSize: 12 }}>
                                {row.error_code}: {value.slice(0, 120)}
                              </Text>
                            ) : (
                              '—'
                            ),
                        },
                      ]}
                    />
                  </AsyncBlock>
                ),
              },
              {
                key: 'offers',
                label: 'Предложения',
                children: (
                  <AsyncBlock loading={offers.loading} error={offers.error}>
                    <Table<Offer>
                      size="small"
                      rowKey="id"
                      dataSource={offers.data?.items ?? []}
                      scroll={{ x: 900 }}
                      pagination={{
                        current: offerPage,
                        pageSize: 25,
                        total: offers.data?.meta.total ?? 0,
                        onChange: setOfferPage,
                        showTotal: (total) => `всего ${total}`,
                      }}
                      columns={[
                        { title: 'Источник', dataIndex: 'source_code' },
                        {
                          title: 'Тип',
                          dataIndex: 'offer_type',
                          render: (v: string) => <Tag>{labelOf(OFFER_TYPE_LABEL, v)}</Tag>,
                        },
                        {
                          title: 'Цена',
                          dataIndex: 'total_price',
                          align: 'right',
                          render: (v: number | null) => money(v),
                        },
                        {
                          title: 'За ночь',
                          dataIndex: 'price_per_night',
                          align: 'right',
                          render: (v: number | null) => (v == null ? '—' : money(v)),
                        },
                        {
                          title: 'Валидность',
                          dataIndex: 'validity_status',
                          render: (value: string) => (
                            <Tag color={value === 'VALID' ? 'success' : 'error'}>
                              {labelOf(VALIDITY_LABEL, value)}
                            </Tag>
                          ),
                        },
                        {
                          title: 'Классификация',
                          dataIndex: 'classification_status',
                          render: (value: string) => (
                            <Tag color={value === 'CLASSIFIED' ? 'default' : 'warning'}>
                              {labelOf(CLASSIFICATION_LABEL, value)}
                            </Tag>
                          ),
                        },
                        {
                          title: 'Исключение',
                          dataIndex: 'exclusion_reason',
                          render: (value: string, row) =>
                            value && value !== 'NONE' ? (
                              <Tag color="orange" title={row.exclusion_detail ?? undefined}>
                                {labelOf(EXCLUSION_LABEL, value)}
                              </Tag>
                            ) : (
                              <Tag color="success">учтено</Tag>
                            ),
                        },
                        {
                          title: 'Выброс',
                          dataIndex: 'is_outlier',
                          render: (value: boolean) => (value ? <Tag color="purple">да</Tag> : '—'),
                        },
                      ]}
                    />
                  </AsyncBlock>
                ),
              },
              {
                key: 'artifacts',
                label: 'Исходные ответы',
                children: (
                  <AsyncBlock loading={artifacts.loading} error={artifacts.error}>
                    <Text type="secondary">
                      Сохраненные исходные ответы источников — доказательная база расчета.
                    </Text>
                    <Table
                      size="small"
                      style={{ marginTop: 12 }}
                      rowKey="id"
                      dataSource={artifacts.data?.raw_responses ?? []}
                      pagination={false}
                      scroll={{ x: 900 }}
                      columns={[
                        { title: 'Источник', dataIndex: 'source_code' },
                        {
                          title: 'Тип',
                          dataIndex: 'offer_type',
                          render: (v: string) => labelOf(OFFER_TYPE_LABEL, v),
                        },
                        { title: 'Код ответа', dataIndex: 'http_status', align: 'right' },
                        {
                          title: 'Задержка',
                          dataIndex: 'latency_ms',
                          align: 'right',
                          render: (v: number | null) => (v == null ? '—' : `${v} мс`),
                        },
                        {
                          title: 'Размер',
                          dataIndex: 'size_bytes',
                          align: 'right',
                          render: (v: number) => `${(v / 1024).toFixed(1)} КБ`,
                        },
                        {
                          title: 'Контрольная сумма',
                          dataIndex: 'checksum_sha256',
                          render: (v: string) => (
                            <span className="tco-monospace">{v?.slice(0, 16)}…</span>
                          ),
                        },
                        {
                          title: 'Хранилище',
                          dataIndex: 'storage_ref',
                          render: (v: string) => (
                            <span className="tco-monospace" style={{ fontSize: 11 }}>
                              {v}
                            </span>
                          ),
                        },
                        {
                          title: 'Хранить до',
                          dataIndex: 'expires_at',
                          render: (v: string | null) => dateTime(v),
                        },
                      ]}
                    />
                  </AsyncBlock>
                ),
              },
            ]}
          />
        </Card>
      </AsyncBlock>

      <Modal
        title="Пересчет снимка"
        open={recalcOpen}
        onCancel={() => setRecalcOpen(false)}
        onOk={recalculate}
        okText="Пересчитать"
        cancelText="Отмена"
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text type="secondary">
            Создается новый расчет по выбранной методике. Исходный снимок и прежние
            результаты не изменяются.
          </Text>
          <Select
            style={{ width: '100%' }}
            placeholder="Профиль расчета (по умолчанию активный)"
            allowClear
            value={profileId}
            onChange={setProfileId}
            options={(profiles.data?.items ?? []).map((p) => ({
              value: p.id,
              label: `${p.label} — ${labelOf(PROFILE_STATUS_LABEL, p.status)}`,
            }))}
          />
        </Space>
      </Modal>
    </>
  );
}
