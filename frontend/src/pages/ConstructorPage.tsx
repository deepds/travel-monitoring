import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  Alert, App, Button, Card, Col, DatePicker, Descriptions, Divider, Form,
  InputNumber, Progress, Row, Select, Space, Tag, Typography,
} from 'antd';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import type { Job, Run } from '@/api/types';
import {
  ConfidenceTag, MetricDisclaimer, PageTitle, RunStatusTag, SyntheticTag,
} from '@/components/common';
import { useAsync, usePolling } from '@/hooks/useAsync';
import { JOB_STATUS_COLOR, JOB_STATUS_LABEL, money, num, percent, score } from '@/utils/format';

const { Text, Paragraph } = Typography;
const TERMINAL = ['SUCCESS', 'FAILED', 'PARTIAL', 'CANCELLED', 'TIMED_OUT'];

interface FormValues {
  origin_city_code: string;
  destination_city_code: string;
  dates: [Dayjs, Dayjs];
  adults: number;
  children_ages?: number[];
  transport_type: 'AVIA' | 'RAIL';
  flight_fare_type?: string;
  rail_class?: string;
  accommodation_type: string;
  stars: string;
  meal_type: string;
  cancellation_filter: string;
  force_refresh?: boolean;
}

export default function ConstructorPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [job, setJob] = useState<Job | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [validationIssues, setValidationIssues] = useState<string[]>([]);
  const transport = Form.useWatch('transport_type', form) ?? 'AVIA';

  const cities = useAsync(() => api.cities(), []);
  const templates = useAsync(() => api.templates(), []);
  const fareTypes = useAsync(() => api.refOptions('fare-types'), []);
  const railClasses = useAsync(() => api.refOptions('rail-classes'), []);
  const accommodation = useAsync(() => api.refOptions('accommodation-types'), []);
  const starsRef = useAsync(() => api.refOptions('stars'), []);
  const meals = useAsync(() => api.refOptions('meal-types'), []);
  const cancellations = useAsync(() => api.refOptions('cancellation-types'), []);

  const cityOptions = useMemo(
    () => (cities.data?.items ?? []).map((c) => ({ value: c.code, label: c.name })),
    [cities.data],
  );

  const asOptions = (items?: { value: string; label: string }[]) =>
    (items ?? []).map((item) => ({ value: item.value, label: item.label }));

  /** Опрос задачи до терминального статуса: расчет идет в фоне. */
  const refreshJob = useCallback(async () => {
    if (!job?.job_id) return;
    try {
      const fresh = await api.calculation(job.job_id);
      setJob(fresh);
      if (fresh.run) setRun(fresh.run);
    } catch {
      // Единичный сбой опроса не должен ломать экран — следующий тик повторит.
    }
  }, [job?.job_id]);

  const polling = Boolean(job && !TERMINAL.includes(job.status));
  usePolling(refreshJob, 2000, polling);

  useEffect(() => {
    if (job && TERMINAL.includes(job.status) && job.status !== 'SUCCESS' && !job.run) {
      message.warning(`Расчет завершился со статусом ${JOB_STATUS_LABEL[job.status] ?? job.status}`);
    }
  }, [job?.status]);

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    setValidationIssues([]);
    setRun(null);
    setJob(null);
    try {
      const [departure, ret] = values.dates;
      const response = await api.createCalculation({
        scenario: {
          origin_city_code: values.origin_city_code,
          destination_city_code: values.destination_city_code,
          departure_date: departure.format('YYYY-MM-DD'),
          return_date: ret.format('YYYY-MM-DD'),
          adults: values.adults,
          children_ages: values.children_ages ?? [],
          transport_type: values.transport_type,
          flight_fare_type: values.transport_type === 'AVIA' ? values.flight_fare_type : null,
          rail_class: values.transport_type === 'RAIL' ? values.rail_class : null,
          accommodation_type: values.accommodation_type,
          stars: values.stars,
          meal_type: values.meal_type,
          cancellation_filter: values.cancellation_filter,
          scenario_type: 'ON_DEMAND',
        },
        force_refresh: values.force_refresh ?? false,
      });

      if (response.cached && response.run) {
        setRun(response.run);
        message.success('Результат получен из кэша');
      } else {
        setJob(response as Job);
        if (response.run) setRun(response.run);
      }
    } catch (exc) {
      if (exc instanceof ApiError) {
        // Невалидный сценарий не доходит до внешних источников — показываем причины.
        const errors = (exc.details?.errors as { message: string }[] | undefined) ?? [];
        setValidationIssues(errors.map((item) => item.message));
        message.error(exc.message);
      } else {
        message.error('Не удалось запустить расчет');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const applyTemplate = (code: string) => {
    const template = templates.data?.items.find((item) => item.code === code);
    if (!template) return;
    const defaults = template.defaults as Record<string, any>;
    form.setFieldsValue({
      adults: defaults.adults ?? 2,
      children_ages: typeof defaults.children_ages === 'string'
        ? String(defaults.children_ages).split(';').filter(Boolean).map(Number)
        : defaults.children_ages,
      transport_type: defaults.transport_type ?? 'AVIA',
      flight_fare_type: defaults.flight_fare_type ?? 'CHEAPEST',
      rail_class: defaults.rail_class,
      accommodation_type: defaults.accommodation_type ?? 'HOTEL',
      stars: String(defaults.stars ?? 'ANY'),
      meal_type: defaults.meal_type ?? 'ANY',
      cancellation_filter: defaults.cancellation_filter ?? 'ANY',
      origin_city_code: defaults.origin_city_code,
      destination_city_code: defaults.destination_city_code,
    });
    message.info(`Шаблон «${template.name}» применен`);
  };

  return (
    <>
      <PageTitle
        title="Расчет сценария"
        subtitle="Управляемый конструктор: доступны только поддерживаемые платформой параметры"
      />
      <MetricDisclaimer />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card title="Параметры путешествия" size="small">
            {templates.data?.items.length ? (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Быстрое заполнение из шаблона
                </Text>
                <Select
                  style={{ width: '100%', marginTop: 4, marginBottom: 16 }}
                  placeholder="Выберите шаблон"
                  allowClear
                  options={templates.data.items.map((t) => ({ value: t.code, label: t.name }))}
                  onChange={(value) => value && applyTemplate(value)}
                />
              </>
            ) : null}

            <Form<FormValues>
              form={form}
              layout="vertical"
              requiredMark={false}
              onFinish={onSubmit}
              initialValues={{
                adults: 2,
                transport_type: 'AVIA',
                flight_fare_type: 'CHEAPEST',
                accommodation_type: 'HOTEL',
                stars: 'ANY',
                meal_type: 'ANY',
                cancellation_filter: 'ANY',
                dates: [dayjs().add(45, 'day'), dayjs().add(50, 'day')],
              }}
            >
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item
                    name="origin_city_code"
                    label="Откуда"
                    rules={[{ required: true, message: 'Выберите город' }]}
                  >
                    <Select options={cityOptions} loading={cities.loading} showSearch optionFilterProp="label" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    name="destination_city_code"
                    label="Куда"
                    dependencies={['origin_city_code']}
                    rules={[
                      { required: true, message: 'Выберите город' },
                      ({ getFieldValue }) => ({
                        validator: (_, value) =>
                          value && value === getFieldValue('origin_city_code')
                            ? Promise.reject(new Error('Города должны различаться'))
                            : Promise.resolve(),
                      }),
                    ]}
                  >
                    <Select options={cityOptions} loading={cities.loading} showSearch optionFilterProp="label" />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item
                name="dates"
                label="Даты поездки"
                rules={[{ required: true, message: 'Укажите даты' }]}
              >
                <DatePicker.RangePicker
                  style={{ width: '100%' }}
                  format="DD.MM.YYYY"
                  disabledDate={(current) => current && current < dayjs().startOf('day')}
                />
              </Form.Item>

              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="adults" label="Взрослых" rules={[{ required: true }]}>
                    <InputNumber min={1} max={9} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="children_ages" label="Возраст детей">
                    <Select
                      mode="multiple"
                      allowClear
                      placeholder="нет"
                      options={Array.from({ length: 18 }, (_, age) => ({
                        value: age,
                        label: `${age} лет`,
                      }))}
                    />
                  </Form.Item>
                </Col>
              </Row>

              <Divider orientation="left" plain style={{ margin: '4px 0 12px' }}>
                Транспорт
              </Divider>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="transport_type" label="Вид" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { value: 'AVIA', label: 'Авиа' },
                        { value: 'RAIL', label: 'Железная дорога' },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  {transport === 'AVIA' ? (
                    <Form.Item name="flight_fare_type" label="Тариф" rules={[{ required: true }]}>
                      <Select options={asOptions(fareTypes.data?.items)} />
                    </Form.Item>
                  ) : (
                    <Form.Item name="rail_class" label="Класс вагона" rules={[{ required: true }]}>
                      <Select options={asOptions(railClasses.data?.items)} />
                    </Form.Item>
                  )}
                </Col>
              </Row>
              {transport === 'AVIA' ? (
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: -8, marginBottom: 12 }}>
                  {fareTypes.data?.note}
                </Text>
              ) : null}

              <Divider orientation="left" plain style={{ margin: '4px 0 12px' }}>
                Проживание
              </Divider>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="accommodation_type" label="Тип" rules={[{ required: true }]}>
                    <Select options={asOptions(accommodation.data?.items)} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="stars" label="Звездность">
                    <Select options={asOptions(starsRef.data?.items)} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="meal_type" label="Питание">
                    <Select options={asOptions(meals.data?.items)} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="cancellation_filter" label="Отмена">
                    <Select options={asOptions(cancellations.data?.items)} />
                  </Form.Item>
                </Col>
              </Row>

              <Space>
                <Button
                  type="primary"
                  htmlType="submit"
                  icon={<PlayCircleOutlined />}
                  loading={submitting || polling}
                >
                  Рассчитать
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => {
                    form.setFieldValue('force_refresh', true);
                    form.submit();
                    form.setFieldValue('force_refresh', false);
                  }}
                  loading={submitting || polling}
                  title="Игнорировать кэш и собрать данные заново"
                >
                  Пересобрать
                </Button>
              </Space>
              <Form.Item name="force_refresh" hidden>
                <input type="hidden" />
              </Form.Item>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={15}>
          {validationIssues.length ? (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message="Сценарий не поддерживается"
              description={
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {validationIssues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              }
            />
          ) : null}

          {job && !run ? (
            <Card size="small" style={{ marginBottom: 16 }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space>
                  <Tag color={JOB_STATUS_COLOR[job.status]}>
                    {JOB_STATUS_LABEL[job.status] ?? job.status}
                  </Tag>
                  <Text type="secondary" className="tco-monospace">
                    {job.job_id}
                  </Text>
                </Space>
                <Progress
                  percent={
                    job.progress?.ratio != null
                      ? Math.round(job.progress.ratio * 100)
                      : polling
                        ? 45
                        : 100
                  }
                  status={job.status === 'FAILED' ? 'exception' : polling ? 'active' : 'normal'}
                />
                <Text type="secondary">
                  {job.progress?.message ?? 'Сбор рыночных предложений и расчет...'}
                </Text>
                {job.error_message ? (
                  <Alert type="error" showIcon message={job.error_code} description={job.error_message} />
                ) : null}
              </Space>
            </Card>
          ) : null}

          {run ? <RunResult run={run} /> : null}

          {!run && !job && !validationIssues.length ? (
            <Card size="small">
              <Paragraph type="secondary" style={{ margin: 0 }}>
                Задайте параметры путешествия слева и запустите расчет. Результат покажет
                расчетную типовую стоимость, диапазон P25–P75, качество данных и уровень
                уверенности с объяснением.
              </Paragraph>
            </Card>
          ) : null}
        </Col>
      </Row>
    </>
  );
}

function RunResult({ run }: { run: Run }) {
  const insufficient = run.confidence_level === 'INSUFFICIENT';

  return (
    <Card
      size="small"
      title="Результат расчета"
      extra={<Link to={`/runs/${run.id}`}>Подробный разбор →</Link>}
    >
      <Space wrap style={{ marginBottom: 12 }}>
        <RunStatusTag status={run.status} />
        <ConfidenceTag level={run.confidence_level} reason={run.confidence.reason} />
        <SyntheticTag visible={run.contains_synthetic_data} />
        {run.served_from_cache ? <Tag color="blue">из кэша</Tag> : null}
        {run.component_statuses.map((status) => (
          <Tag key={status} color="warning">
            {status}
          </Tag>
        ))}
      </Space>

      {insufficient ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="Недостаточно данных для полноценной оценки"
          description={
            run.confidence.reason ??
            'Итог не может быть показан как полноценная расчетная стоимость.'
          }
        />
      ) : (
        <div style={{ marginBottom: 16 }}>
          <Text type="secondary">Расчетная типовая стоимость</Text>
          <div style={{ fontSize: 34, fontWeight: 600, lineHeight: 1.2 }}>
            {money(run.total_estimated_cost)}
          </div>
          <Text type="secondary">
            Диапазон: {money(run.total.p25)} — {money(run.total.p75)} · на человека{' '}
            {money(run.price_per_person)} · путешественников {run.traveler_count}
          </Text>
          {run.confidence.reason ? (
            <div style={{ marginTop: 6 }}>
              <Text type="secondary">Причина: {run.confidence.reason}</Text>
            </div>
          ) : null}
        </div>
      )}

      <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered>
        <Descriptions.Item label="Транспорт (медиана)">
          {money(run.transport.median)}
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>
            P25–P75: {money(run.transport.p25)} — {money(run.transport.p75)} · источников{' '}
            {run.transport.source_count} · предложений {run.transport.offer_count}
            {run.transport.disagreement != null
              ? ` · расхождение ${percent(run.transport.disagreement)}`
              : ''}
          </Text>
        </Descriptions.Item>
        <Descriptions.Item label="Проживание (медиана)">
          {money(run.accommodation.median)}
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>
            P25–P75: {money(run.accommodation.p25)} — {money(run.accommodation.p75)} · источников{' '}
            {run.accommodation.source_count} · предложений {run.accommodation.offer_count}
            {run.accommodation.disagreement != null
              ? ` · расхождение ${percent(run.accommodation.disagreement)}`
              : ''}
          </Text>
        </Descriptions.Item>
        <Descriptions.Item label="Quality Score">{score(run.quality_score)}</Descriptions.Item>
        <Descriptions.Item label="Доля транспорта">
          {percent(run.total.transport_share)}
        </Descriptions.Item>
        <Descriptions.Item label="Предложений учтено">
          {num(run.offers.valid)} · исключено {num(run.offers.excluded)} · выбросов{' '}
          {num(run.offers.outliers)}
        </Descriptions.Item>
        <Descriptions.Item label="Источники">
          {run.sources.codes.map((code) => (
            <Tag key={code}>{code}</Tag>
          ))}
        </Descriptions.Item>
        <Descriptions.Item label="Профиль методики">
          {run.profile.code}@{run.profile.version}
        </Descriptions.Item>
        <Descriptions.Item label="Снимок рынка">
          {run.market_snapshot_id ? (
            <Link to={`/snapshots/${run.market_snapshot_id}`} className="tco-monospace">
              {run.market_snapshot_id.slice(0, 8)}…
            </Link>
          ) : (
            '—'
          )}
        </Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
