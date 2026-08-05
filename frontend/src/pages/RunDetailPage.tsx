import { Card, Col, Descriptions, Row, Space, Table, Tabs, Tag, Typography } from 'antd';
import { Link, useParams } from 'react-router-dom';

import { api } from '@/api/client';
import type { SourceBreakdownRow } from '@/api/types';
import { AsyncBlock, MetricDisclaimer, PageTitle } from '@/components/common';
import { OffersTable } from '@/components/OffersTable';
import { TripBuilder } from '@/components/TripBuilder';
import { RailComparisonTable } from '@/components/RailComparisonTable';
import { useAsync } from '@/hooks/useAsync';
import {
  COMPONENT_LABEL, COMPONENT_STATUS_LABEL, FILTER_REASON_LABEL,
  dateTime, labelOf, money, num, percent,
} from '@/utils/format';

const { Text } = Typography;

export default function RunDetailPage() {
  const { id = '' } = useParams();
  const run = useAsync(() => api.run(id), [id]);
  const explain = useAsync(() => api.runExplain(id), [id]);
  const breakdown = useAsync(() => api.runSourceBreakdown(id), [id]);

  const data = run.data;
  const rows: SourceBreakdownRow[] = (breakdown.data?.rows as SourceBreakdownRow[]) ?? [];

  return (
    <>
      <PageTitle
        title="Разбор расчета"
        subtitle={data ? `${data.scenario_code} · ${data.scenario_name}` : undefined}
        extra={
          data?.scenario_id ? (
            <Link to={`/scenarios/${data.scenario_id}`}>К сценарию →</Link>
          ) : null
        }
      />

      <AsyncBlock loading={run.loading} error={run.error}>
        <MetricDisclaimer text={data?.disclaimer} />

        {/*
          В шапке остаются только предупреждения, меняющие трактовку цифры.
          Версии профиля, движка и нормализации — техническая метаинформация;
          она есть во вкладке «Объяснение» и не нужна на первом экране.
        */}
        <Space wrap style={{ marginBottom: 16 }}>
          {data?.served_from_cache ? <Tag color="blue">из кэша</Tag> : null}
          {(data?.component_statuses ?? []).map((status) => (
            <Tag key={status} color="warning">
              {labelOf(COMPONENT_STATUS_LABEL, status)}
            </Tag>
          ))}
        </Space>

        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24}>
            <Card size="small" title="Стоимость">
              <div style={{ marginBottom: 16 }}>
                <Text type="secondary">Расчетная типовая стоимость путешествия</Text>
                <div className="tco-headline" style={{ fontSize: 32 }}>
                  {money(data?.total_estimated_cost)}
                </div>
                <Text type="secondary">
                  P25–P75 {money(data?.total.p25)} — {money(data?.total.p75)}
                  {data?.total.min != null && data?.total.max != null ? (
                    <> · размах {money(data.total.min)} — {money(data.total.max)}</>
                  ) : null}
                  {' '}· на человека {money(data?.price_per_person)}
                </Text>
              </div>

              <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered>
                <Descriptions.Item label="Транспорт">
                  <b>{money(data?.transport.median)}</b>
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    P25–P75 {money(data?.transport.p25)} — {money(data?.transport.p75)}
                    {data?.transport.min != null && data?.transport.max != null ? (
                      <>
                        <br />
                        размах {money(data.transport.min)} — {money(data.transport.max)}
                      </>
                    ) : null}
                    <br />
                    источников {data?.transport.source_count}, предложений{' '}
                    {data?.transport.offer_count}
                    {data?.transport.disagreement != null
                      ? `, расхождение ${percent(data.transport.disagreement)}`
                      : ''}
                  </Text>
                </Descriptions.Item>
                <Descriptions.Item label="Проживание">
                  <b>{money(data?.accommodation.median)}</b>
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    P25–P75 {money(data?.accommodation.p25)} — {money(data?.accommodation.p75)}
                    {data?.accommodation.min != null && data?.accommodation.max != null ? (
                      <>
                        <br />
                        размах {money(data.accommodation.min)} — {money(data.accommodation.max)}
                      </>
                    ) : null}
                    <br />
                    источников {data?.accommodation.source_count}, предложений{' '}
                    {data?.accommodation.offer_count}
                    {data?.accommodation.disagreement != null
                      ? `, расхождение ${percent(data.accommodation.disagreement)}`
                      : ''}
                  </Text>
                </Descriptions.Item>
                <Descriptions.Item label="Доля транспорта">
                  {percent(data?.total.transport_share)}
                </Descriptions.Item>
                <Descriptions.Item label="Путешественников">
                  {data?.traveler_count}
                </Descriptions.Item>
                <Descriptions.Item label="Горизонт бронирования">
                  {data?.lead_time_days} дн.
                </Descriptions.Item>
                <Descriptions.Item label="Длительность расчета">
                  {data?.duration_ms ? `${data.duration_ms} мс` : '—'}
                </Descriptions.Item>
                <Descriptions.Item label="Снимок рынка">
                  {data?.market_snapshot_id ? (
                    <Link to={`/snapshots/${data.market_snapshot_id}`} className="tco-monospace">
                      {data.market_snapshot_id.slice(0, 8)}…
                    </Link>
                  ) : (
                    '—'
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="Завершен">
                  {dateTime(data?.completed_at)}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
        </Row>

        <Card size="small">
          <Tabs
            items={[
              {
                key: 'trip',
                label: 'Собрать поездку',
                children: <TripBuilder runId={id} />,
              },
              {
                key: 'offers',
                label: 'Предложения',
                children: <OffersTable runId={id} />,
              },
              {
                key: 'rail',
                label: 'Сравнение источников (ЖД)',
                children: <RailComparisonTable runId={id} />,
              },
              {
                key: 'sources',
                label: 'Источники',
                children: (
                  <AsyncBlock
                    loading={breakdown.loading}
                    error={breakdown.error}
                    empty={rows.length === 0}
                  >
                    <Table<SourceBreakdownRow>
                      size="small"
                      rowKey={(row) => `${row.component}-${row.source_code}`}
                      dataSource={rows}
                      pagination={false}
                      scroll={{ x: 900 }}
                      columns={[
                        {
                          title: 'Компонент',
                          dataIndex: 'component',
                          render: (value: string) => (
                            <Tag color={value === 'TRANSPORT' ? 'blue' : 'green'}>
                              {labelOf(COMPONENT_LABEL, value)}
                            </Tag>
                          ),
                        },
                        { title: 'Источник', dataIndex: 'source_code' },
                        {
                          title: 'Допущен',
                          dataIndex: 'eligible',
                          render: (value: boolean) =>
                            value ? <Tag color="success">да</Tag> : <Tag color="error">нет</Tag>,
                        },
                        {
                          title: 'Медиана источника',
                          dataIndex: 'statistic',
                          align: 'right',
                          render: (value: number | null) => money(value),
                        },
                        {
                          title: 'Предложений',
                          dataIndex: 'offer_count',
                          align: 'right',
                          render: (value: number) => num(value),
                        },
                        {
                          title: 'Валидных',
                          dataIndex: 'valid_offer_count',
                          align: 'right',
                          render: (value: number) => num(value),
                        },
                        {
                          title: 'Неклассиф.',
                          dataIndex: 'unclassified_offer_count',
                          align: 'right',
                          render: (value: number) => num(value),
                        },
                        {
                          title: 'Причины недопуска',
                          dataIndex: 'ineligibility_reasons',
                          render: (value: string[]) =>
                            value?.length ? (
                              <ul style={{ margin: 0, paddingLeft: 16 }}>
                                {value.map((reason) => (
                                  <li key={reason}>
                                    <Text type="danger" style={{ fontSize: 12 }}>
                                      {reason}
                                    </Text>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <Text type="secondary">—</Text>
                            ),
                        },
                      ]}
                    />
                  </AsyncBlock>
                ),
              },
              {
                key: 'explain',
                label: 'Объяснение',
                children: (
                  <AsyncBlock loading={explain.loading} error={explain.error}>
                    <ExplainView payload={explain.data?.explainability ?? {}} />
                  </AsyncBlock>
                ),
              },
              {
                key: 'errors',
                label: `Ошибки сбора (${(data?.error_summary ?? []).length})`,
                children: (data?.error_summary ?? []).length ? (
                  <pre className="tco-monospace" style={{ whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(data?.error_summary, null, 2)}
                  </pre>
                ) : (
                  <Text type="secondary">Ошибок при сборе не зафиксировано</Text>
                ),
              },
            ]}
          />
        </Card>
      </AsyncBlock>
    </>
  );
}

function ExplainView({ payload }: { payload: Record<string, any> }) {
  const selection = payload.selection ?? {};

  return (
    <>
      {Object.entries(selection).map(([component, info]: [string, any]) => (
        <Card
          key={component}
          size="small"
          title={component === 'TRANSPORT' ? 'Отбор предложений: транспорт' : 'Отбор предложений: проживание'}
          style={{ marginBottom: 12 }}
        >
          <Descriptions size="small" column={{ xs: 2, md: 4 }} bordered>
            <Descriptions.Item label="Получено">{num(info.total)}</Descriptions.Item>
            <Descriptions.Item label="Невалидных">{num(info.invalid)}</Descriptions.Item>
            <Descriptions.Item label="Отсеяно фильтром">{num(info.filtered_out)}</Descriptions.Item>
            <Descriptions.Item label="Дубликатов">{num(info.duplicates)}</Descriptions.Item>
            <Descriptions.Item label="Выбросов">{num(info.outliers)}</Descriptions.Item>
            <Descriptions.Item label="Учтено в расчете">{num(info.eligible)}</Descriptions.Item>
          </Descriptions>
          {info.filter_reasons && Object.keys(info.filter_reasons).length ? (
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">Причины отсева: </Text>
              {Object.entries(info.filter_reasons).map(([reason, count]) => (
                <Tag key={reason}>
                  {labelOf(FILTER_REASON_LABEL, reason)}: {String(count)}
                </Tag>
              ))}
            </div>
          ) : null}
        </Card>
      ))}

      <details>
        <summary style={{ cursor: 'pointer', marginBottom: 8 }}>
          Полные данные объяснимости
        </summary>
        <pre className="tco-monospace" style={{ whiteSpace: 'pre-wrap', maxHeight: 420, overflow: 'auto' }}>
          {JSON.stringify(payload, null, 2)}
        </pre>
      </details>
    </>
  );
}
