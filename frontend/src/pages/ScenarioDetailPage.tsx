import { PlayCircleOutlined } from '@ant-design/icons';
import { App, Button, Card, Col, Descriptions, Row, Table, Tag } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useMemo } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import { AsyncBlock, ConfidenceTag, PageTitle, RunStatusTag, SyntheticTag } from '@/components/common';
import { useAuth } from '@/auth/AuthContext';
import { useAsync } from '@/hooks/useAsync';
import {
  ACCOMMODATION_LABEL, TRANSPORT_LABEL, dateOnly, dateTime, money, score, starsLabel,
} from '@/utils/format';

export default function ScenarioDetailPage() {
  const { id = '' } = useParams();
  const { message } = App.useApp();
  const { can } = useAuth();
  const navigate = useNavigate();

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
          data: [...points].reverse().map((p) => p.total_estimated_cost),
        },
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

  return (
    <>
      <PageTitle
        title={data?.name ?? 'Сценарий'}
        subtitle={data?.code}
        extra={
          can('ANALYST') ? (
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={runNow}>
              Рассчитать сейчас
            </Button>
          ) : null
        }
      />

      <AsyncBlock loading={scenario.loading} error={scenario.error}>
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
                  <Tag>{TRANSPORT_LABEL[data?.transport_type ?? ''] ?? data?.transport_type}</Tag>
                  {data?.flight_fare_type ? <Tag>{data.flight_fare_type}</Tag> : null}
                  {data?.rail_class ? <Tag>{data.rail_class}</Tag> : null}
                </Descriptions.Item>
                <Descriptions.Item label="Размещение">
                  {ACCOMMODATION_LABEL[data?.accommodation_type ?? ''] ?? data?.accommodation_type} ·{' '}
                  {starsLabel(data?.stars)}
                </Descriptions.Item>
                <Descriptions.Item label="Питание / отмена">
                  {data?.meal_type} · {data?.cancellation_filter}
                </Descriptions.Item>
                <Descriptions.Item label="Режим">
                  <Tag>{data?.scenario_type}</Tag>
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
                { title: 'Тип', dataIndex: 'run_type', render: (v: string) => <Tag>{v}</Tag> },
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
                  title: 'Quality',
                  dataIndex: 'quality_score',
                  align: 'right',
                  render: (value: number | null) => score(value),
                },
                {
                  title: 'Уверенность',
                  dataIndex: 'confidence_level',
                  render: (value: any) => <ConfidenceTag level={value} />,
                },
                {
                  title: '',
                  dataIndex: 'contains_synthetic_data',
                  render: (value: boolean) => <SyntheticTag visible={value} />,
                },
              ]}
            />
          </AsyncBlock>
        </Card>
      </AsyncBlock>
    </>
  );
}
