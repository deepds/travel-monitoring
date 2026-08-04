import { PlayCircleOutlined } from '@ant-design/icons';
import { Alert, App, Button, Card, Col, Descriptions, Row, Space, Switch, Table, Tag } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import {
  AsyncBlock, ConfidenceTag, LabelWithHint, PageTitle, RunStatusTag,
} from '@/components/common';
import { ScenarioDeleteModal } from '@/components/ScenarioDialogs';
import { TOTAL_COLOR, rangeSeries } from '@/utils/charts';
import { useAuth } from '@/auth/AuthContext';
import { useAsync } from '@/hooks/useAsync';
import {
  ACCOMMODATION_LABEL, CANCELLATION_LABEL, FARE_TYPE_LABEL, MEAL_LABEL, RAIL_CLASS_LABEL,
  RUN_TYPE_LABEL, SCENARIO_TYPE_LABEL, TRANSPORT_LABEL, dateOnly, dateTime, labelOf, money,
  score, starsLabel,
} from '@/utils/format';
import { QUALITY_SCORE_SHORT_HINT } from '@/utils/hints';

export default function ScenarioDetailPage() {
  const { id = '' } = useParams();
  const { message } = App.useApp();
  const { can } = useAuth();
  const navigate = useNavigate();

  const [deleting, setDeleting] = useState(false);

  const scenario = useAsync(() => api.scenario(id), [id]);
  const history = useAsync(() => api.scenarioHistory(id, { limit: 200 }), [id]);

  const points = history.data?.items ?? [];

  const chartOption = useMemo(
    () => ({
      tooltip: { trigger: 'axis' },
      legend: { data: ['Итого', 'Транспорт', 'Проживание'], bottom: 0 },
      grid: { left: 60, right: 24, top: 24, bottom: 48 },
      xAxis: {
        type: 'category',
        data: [...points].reverse().map((p) => p.observation_date),
      },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${Math.round(v / 1000)}к` } },
      series: [
        {
          name: 'Итого',
          type: 'line',
          smooth: true,
          lineStyle: { width: 3 },
          color: TOTAL_COLOR,
          data: [...points].reverse().map((p) => p.total_estimated_cost),
        },
        ...rangeSeries(
          [...points].reverse().map((p) => p.total_min),
          [...points].reverse().map((p) => p.total_max),
        ),
        {
          name: 'Транспорт',
          type: 'line',
          smooth: true,
          data: [...points].reverse().map((p) => p.transport_median),
        },
        {
          name: 'Проживание',
          type: 'line',
          smooth: true,
          data: [...points].reverse().map((p) => p.hotel_median),
        },
      ],
    }),
    [points],
  );

  const runNow = async () => {
    try {
      const response = await api.createCalculation({ scenario_id: id });
      message.success('Расчет запущен');
      if (response.run?.id) navigate(`/runs/${response.run.id}`);
      else navigate('/jobs');
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось запустить расчет');
    }
  };

  const data = scenario.data;

  const toggleActive = async () => {
    if (!data) return;
    try {
      if (data.is_active) await api.deactivateScenario(data.id);
      else await api.activateScenario(data.id);
      message.success(
        data.is_active
          ? 'Сценарий снят с планового наблюдения'
          : 'Сценарий поставлен на плановое наблюдение',
      );
      scenario.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось изменить сценарий');
    }
  };

  return (
    <>
      <PageTitle
        title={data?.name ?? 'Сценарий'}
        subtitle={data?.code}
        extra={
          <Space wrap>
            {can('ADMIN') && data ? (
              <Space size={6}>
                <Switch size="small" checked={data.is_active} onChange={toggleActive} />
                <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                  {data.is_active ? 'на наблюдении' : 'снят с наблюдения'}
                </span>
              </Space>
            ) : null}
            {can('ANALYST') ? (
              <Button type="primary" icon={<PlayCircleOutlined />} onClick={runNow}>
                Рассчитать сейчас
              </Button>
            ) : null}
            {can('ADMIN') && data ? (
              <Button danger onClick={() => setDeleting(true)}>
                Удалить
              </Button>
            ) : null}
          </Space>
        }
      />

      <AsyncBlock loading={scenario.loading} error={scenario.error}>
        {data && !data.is_active ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="Сценарий снят с планового наблюдения"
            description="Новые данные по нему не собираются. Уже накопленная история сохраняется и остается доступной."
          />
        ) : null}

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={10}>
            <Card size="small" title="Параметры">
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="Маршрут">
                  {data?.origin?.name} → {data?.destination?.name}
                </Descriptions.Item>
                <Descriptions.Item label="Даты">
                  {dateOnly(data?.departure_date)} — {dateOnly(data?.return_date)} ({data?.nights} ноч.)
                </Descriptions.Item>
                <Descriptions.Item label="Состав">
                  {data?.adults} взр.
                  {data?.children_ages?.length
                    ? `, дети: ${data.children_ages.join(', ')} лет`
                    : ''}{' '}
                  (всего {data?.traveler_count})
                </Descriptions.Item>
                <Descriptions.Item label="Транспорт">
                  {data?.transport_type ? (
                    <>
                      <Tag>{TRANSPORT_LABEL[data.transport_type] ?? data.transport_type}</Tag>
                      {data.flight_fare_type ? (
                        <Tag>{labelOf(FARE_TYPE_LABEL, data.flight_fare_type)}</Tag>
                      ) : null}
                      {data.rail_class ? (
                        <Tag>{labelOf(RAIL_CLASS_LABEL, data.rail_class)}</Tag>
                      ) : null}
                    </>
                  ) : (
                    <Tag color="default">не наблюдается</Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="Размещение">
                  {data?.accommodation_type ? (
                    <>
                      {ACCOMMODATION_LABEL[data.accommodation_type] ?? data.accommodation_type} ·{' '}
                      {starsLabel(data.stars)}
                    </>
                  ) : (
                    <Tag color="default">не наблюдается</Tag>
                  )}
                </Descriptions.Item>
                {data?.accommodation_type ? (
                  <Descriptions.Item label="Питание / отмена">
                    {labelOf(MEAL_LABEL, data.meal_type)} ·{' '}
                    {labelOf(CANCELLATION_LABEL, data.cancellation_filter)}
                  </Descriptions.Item>
                ) : null}
                <Descriptions.Item label="Режим">
                  <Tag>{labelOf(SCENARIO_TYPE_LABEL, data?.scenario_type)}</Tag>
                  {data?.is_active ? <Tag color="success">активен</Tag> : <Tag>выключен</Tag>}
                </Descriptions.Item>
                <Descriptions.Item label="Период активности">
                  {dateOnly(data?.active_from)} — {dateOnly(data?.active_until)}
                </Descriptions.Item>
                <Descriptions.Item label="Отпечаток">
                  <span className="tco-monospace">{data?.fingerprint?.slice(0, 24)}…</span>
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>

          <Col xs={24} lg={14}>
            <Card size="small" title="Динамика стоимости">
              <AsyncBlock
                loading={history.loading}
                error={history.error}
                empty={points.length === 0}
                emptyText="Расчетов по сценарию еще не было"
                minHeight={300}
              >
                <ReactECharts option={chartOption} style={{ height: 300 }} notMerge />
              </AsyncBlock>
            </Card>
          </Col>
        </Row>

        <Card size="small" title="История расчетов" style={{ marginTop: 16 }}>
          <AsyncBlock
            loading={history.loading}
            error={history.error}
            empty={points.length === 0}
            emptyText="Нет расчетов"
          >
            <Table
              size="small"
              rowKey="run_id"
              dataSource={points}
              scroll={{ x: 1000 }}
              pagination={{ pageSize: 20, showTotal: (t) => `всего ${t}` }}
              columns={[
                {
                  title: 'Дата наблюдения',
                  dataIndex: 'observation_date',
                  render: (value: string, row: any) => (
                    <Link to={`/runs/${row.run_id}`}>{dateOnly(value)}</Link>
                  ),
                },
                { title: 'Запуск', dataIndex: 'started_at', render: (v: string) => dateTime(v) },
                {
                  title: 'Тип',
                  dataIndex: 'run_type',
                  render: (v: string) => <Tag>{labelOf(RUN_TYPE_LABEL, v)}</Tag>,
                },
                {
                  title: 'Статус',
                  dataIndex: 'status',
                  render: (value: any) => <RunStatusTag status={value} />,
                },
                {
                  title: 'Итого',
                  dataIndex: 'total_estimated_cost',
                  align: 'right',
                  render: (value: number | null) => <b>{money(value)}</b>,
                },
                {
                  title: 'Транспорт',
                  dataIndex: 'transport_median',
                  align: 'right',
                  render: (value: number | null) => money(value),
                },
                {
                  title: 'Проживание',
                  dataIndex: 'hotel_median',
                  align: 'right',
                  render: (value: number | null) => money(value),
                },
                {
                  title: <LabelWithHint text="Качество" hint={QUALITY_SCORE_SHORT_HINT} />,
                  dataIndex: 'quality_score',
                  align: 'right',
                  render: (value: number | null) => score(value),
                },
                {
                  title: 'Уверенность',
                  dataIndex: 'confidence_level',
                  render: (value: any) => <ConfidenceTag level={value} />,
                },
              ]}
            />
          </AsyncBlock>
        </Card>
      </AsyncBlock>

      <ScenarioDeleteModal
        scenario={deleting ? (data ?? null) : null}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/scenarios')}
      />
    </>
  );
}
