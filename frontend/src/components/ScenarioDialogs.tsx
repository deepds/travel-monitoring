/**
 * Диалоги жизненного цикла сценария: создание и удаление.
 *
 * Вынесены в отдельный модуль, потому что вызываются с двух экранов — из
 * каталога сценариев и из карточки конкретного сценария, — и расхождение в
 * формулировках между ними читалось бы как разное поведение.
 */

import { ExclamationCircleOutlined } from '@ant-design/icons';
import {
  Alert, App, Col, DatePicker, Descriptions, Form, Input, InputNumber, Modal, Radio,
  Row, Select, Switch, Typography,
} from 'antd';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import { useEffect, useState } from 'react';

import { ApiError, api } from '@/api/client';
import type { ScenarioBrief, ScenarioFootprint } from '@/api/types';
import { useAsync } from '@/hooks/useAsync';
import { num } from '@/utils/format';

const { Text, Paragraph } = Typography;

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
  name?: string;
  is_active: boolean;
}

interface CreateProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

/**
 * Создание сценария наблюдения.
 *
 * Конструктор управляемый: все значения приходят из справочников платформы,
 * даты выбираются календарем. Ручной ввод исключен намеренно — сценарий с
 * неподдерживаемым параметром не дошел бы до источников, а выяснилось бы это
 * только на первом сборе.
 */
export function ScenarioCreateModal({ open, onClose, onCreated }: CreateProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const transport = Form.useWatch('transport_type', form) ?? 'AVIA';
  const accommodation = Form.useWatch('accommodation_type', form);

  const cities = useAsync(() => api.cities(), []);
  const fareTypes = useAsync(() => api.refOptions('fare-types'), []);
  const railClasses = useAsync(() => api.refOptions('rail-classes'), []);
  const accommodationTypes = useAsync(() => api.refOptions('accommodation-types'), []);
  const starsRef = useAsync(() => api.refOptions('stars'), []);
  const meals = useAsync(() => api.refOptions('meal-types'), []);
  const cancellations = useAsync(() => api.refOptions('cancellation-types'), []);
  const horizon = useAsync(() => api.horizon(), []);

  const cityOptions = (cities.data?.items ?? []).map((city) => ({
    value: city.code,
    label: city.name,
  }));
  const asOptions = (items?: { value: string; label: string }[]) =>
    (items ?? []).map((item) => ({ value: item.value, label: item.label }));

  // Звездность применима не ко всякому размещению: для хостела или апартаментов
  // справочник отдает NOT_APPLICABLE, и требовать категорию нельзя.
  const starsApplicable = accommodation === 'HOTEL' || accommodation === 'SANATORIUM';

  useEffect(() => {
    if (!starsApplicable) form.setFieldValue('stars', 'NOT_APPLICABLE');
    else if (form.getFieldValue('stars') === 'NOT_APPLICABLE') form.setFieldValue('stars', 'ANY');
  }, [starsApplicable]);

  const maxDate = horizon.data?.max_supported_date
    ? dayjs(horizon.data.max_supported_date as string)
    : undefined;

  const submit = async (values: FormValues) => {
    setSubmitting(true);
    try {
      const [departure, back] = values.dates;
      const created = await api.createScenario({
        origin_city_code: values.origin_city_code,
        destination_city_code: values.destination_city_code,
        departure_date: departure.format('YYYY-MM-DD'),
        return_date: back.format('YYYY-MM-DD'),
        adults: values.adults,
        children_ages: values.children_ages ?? [],
        transport_type: values.transport_type,
        flight_fare_type: values.transport_type === 'AVIA' ? values.flight_fare_type : null,
        rail_class: values.transport_type === 'RAIL' ? values.rail_class : null,
        accommodation_type: values.accommodation_type,
        stars: values.stars,
        meal_type: values.meal_type,
        cancellation_filter: values.cancellation_filter,
        name: values.name?.trim() || null,
        scenario_type: 'MONITORING',
      });

      // Отпечаток сценария стабилен: повтор тех же параметров возвращает
      // существующую запись, а не создает дубль.
      if ((created as unknown as { created?: boolean }).created === false) {
        message.warning(`Сценарий с такими параметрами уже есть: ${created.code}`);
      } else if (!values.is_active) {
        await api.deactivateScenario(created.id);
        message.success(`Сценарий ${created.code} создан и оставлен выключенным`);
      } else {
        message.success(`Сценарий ${created.code} создан и поставлен на наблюдение`);
      }

      form.resetFields();
      onCreated();
      onClose();
    } catch (exc) {
      if (exc instanceof ApiError) {
        const errors = (exc.details?.errors as { message: string }[] | undefined) ?? [];
        message.error(errors.length ? errors.map((item) => item.message).join('; ') : exc.message);
      } else {
        message.error('Не удалось создать сценарий');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="Новый сценарий наблюдения"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="Создать"
      cancelText="Отмена"
      confirmLoading={submitting}
      width={720}
      destroyOnClose
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Активный сценарий попадает в плановый сбор"
        description="Наблюдение идет ежечасно. Выключенный сценарий сохраняется в каталоге, но данные по нему не собираются."
      />

      <Form<FormValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={submit}
        initialValues={{
          adults: 2,
          transport_type: 'AVIA',
          flight_fare_type: 'CHEAPEST',
          accommodation_type: 'HOTEL',
          stars: 'ANY',
          meal_type: 'ANY',
          cancellation_filter: 'ANY',
          is_active: true,
          dates: [dayjs().add(45, 'day'), dayjs().add(50, 'day')],
        }}
      >
        <Row gutter={12}>
          <Col xs={24} sm={12}>
            <Form.Item
              name="origin_city_code"
              label="Откуда"
              rules={[{ required: true, message: 'Выберите город' }]}
            >
              <Select
                options={cityOptions}
                loading={cities.loading}
                showSearch
                optionFilterProp="label"
                placeholder="Город отправления"
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
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
              <Select
                options={cityOptions}
                loading={cities.loading}
                showSearch
                optionFilterProp="label"
                placeholder="Город назначения"
              />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item
          name="dates"
          label="Даты поездки"
          rules={[{ required: true, message: 'Укажите даты' }]}
          extra={
            maxDate ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                Источники отдают цены до {maxDate.format('DD.MM.YYYY')}
              </Text>
            ) : null
          }
        >
          <DatePicker.RangePicker
            style={{ width: '100%' }}
            format="DD.MM.YYYY"
            disabledDate={(current) =>
              current &&
              (current < dayjs().startOf('day') || (maxDate ? current > maxDate : false))
            }
          />
        </Form.Item>

        <Row gutter={12}>
          <Col xs={24} sm={8}>
            <Form.Item name="adults" label="Взрослых" rules={[{ required: true }]}>
              <InputNumber min={1} max={9} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={16}>
            <Form.Item name="children_ages" label="Возраст детей">
              <Select
                mode="multiple"
                allowClear
                placeholder="без детей"
                options={Array.from({ length: 18 }, (_, age) => ({
                  value: age,
                  label: `${age} лет`,
                }))}
              />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={12}>
          <Col xs={24} sm={12}>
            <Form.Item name="transport_type" label="Транспорт" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'AVIA', label: 'Авиа' },
                  { value: 'RAIL', label: 'Железная дорога' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            {transport === 'AVIA' ? (
              <Form.Item
                name="flight_fare_type"
                label="Тариф"
                rules={[{ required: true, message: 'Выберите тариф' }]}
              >
                <Select options={asOptions(fareTypes.data?.items)} loading={fareTypes.loading} />
              </Form.Item>
            ) : (
              <Form.Item
                name="rail_class"
                label="Класс вагона"
                rules={[{ required: true, message: 'Выберите класс вагона' }]}
              >
                <Select options={asOptions(railClasses.data?.items)} loading={railClasses.loading} />
              </Form.Item>
            )}
          </Col>
        </Row>

        <Row gutter={12}>
          <Col xs={24} sm={12}>
            <Form.Item name="accommodation_type" label="Размещение" rules={[{ required: true }]}>
              <Select
                options={asOptions(accommodationTypes.data?.items)}
                loading={accommodationTypes.loading}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item
              name="stars"
              label="Звездность"
              extra={
                !starsApplicable ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Для этого типа размещения звезды неприменимы
                  </Text>
                ) : null
              }
            >
              <Select
                options={asOptions(starsRef.data?.items)}
                loading={starsRef.loading}
                disabled={!starsApplicable}
              />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={12}>
          <Col xs={24} sm={12}>
            <Form.Item name="meal_type" label="Питание">
              <Select options={asOptions(meals.data?.items)} loading={meals.loading} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="cancellation_filter" label="Условия отмены">
              <Select
                options={asOptions(cancellations.data?.items)}
                loading={cancellations.loading}
              />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={12}>
          <Col xs={24} sm={16}>
            <Form.Item
              name="name"
              label="Название"
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Необязательно: если не задать, соберется из маршрута и дат
                </Text>
              }
            >
              <Input maxLength={255} placeholder="например, Москва → Сочи, сентябрь" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={8}>
            <Form.Item name="is_active" label="Наблюдение" valuePropName="checked">
              <Switch checkedChildren="включено" unCheckedChildren="выключено" />
            </Form.Item>
          </Col>
        </Row>
      </Form>
    </Modal>
  );
}

interface DeleteProps {
  scenario: ScenarioBrief | null;
  onClose: () => void;
  onDeleted: () => void;
}

/**
 * Удаление сценария с выбором судьбы накопленных данных.
 *
 * Наблюдения невосполнимы — источники не отдают цены задним числом, — поэтому
 * режим по умолчанию сохраняет историю, а объем уничтожаемого показывается
 * числами до подтверждения.
 */
export function ScenarioDeleteModal({ scenario, onClose, onDeleted }: DeleteProps) {
  const { message } = App.useApp();
  const [purge, setPurge] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const footprint = useAsync(
    () => (scenario ? api.scenarioFootprint(scenario.id) : Promise.resolve(null)),
    [scenario?.id],
  );

  useEffect(() => {
    if (scenario) setPurge(false);
  }, [scenario?.id]);

  const data = footprint.data as ScenarioFootprint | null;
  const hasData = Boolean(data && (data.snapshots || data.runs || data.offers));

  const confirm = async () => {
    if (!scenario) return;
    setSubmitting(true);
    try {
      const result = await api.deleteScenario(scenario.id, purge);
      message.success(result.message);
      onDeleted();
      onClose();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось удалить сценарий');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={scenario ? `Удаление сценария ${scenario.code}` : ''}
      open={Boolean(scenario)}
      onCancel={onClose}
      onOk={confirm}
      okText={purge ? 'Удалить вместе с данными' : 'Удалить запись'}
      cancelText="Отмена"
      okButtonProps={{ danger: true }}
      confirmLoading={submitting}
      width={620}
      destroyOnClose
    >
      <Paragraph type="secondary">
        Сценарий снимается с наблюдения в обоих случаях. Различие — в судьбе уже собранного.
      </Paragraph>

      <Descriptions
        size="small"
        column={{ xs: 1, sm: 3 }}
        bordered
        style={{ marginBottom: 16 }}
        title="Накоплено по сценарию"
      >
        <Descriptions.Item label="Снимков рынка">{num(data?.snapshots)}</Descriptions.Item>
        <Descriptions.Item label="Расчетов">{num(data?.runs)}</Descriptions.Item>
        <Descriptions.Item label="Предложений">{num(data?.offers)}</Descriptions.Item>
      </Descriptions>

      <Radio.Group
        value={purge}
        onChange={(event) => setPurge(event.target.value)}
        style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
      >
        <Radio value={false}>
          <b>Удалить только запись каталога</b>
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>
            Сценарий исчезнет из списка и перестанет собираться. Снимки рынка и расчеты
            останутся — история наблюдений не пострадает.
          </Text>
        </Radio>
        <Radio value={true}>
          <b>Удалить вместе с накопленными данными</b>
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>
            Снимки рынка, расчеты, предложения и сохраненные ответы источников будут
            уничтожены.
          </Text>
        </Radio>
      </Radio.Group>

      {purge && hasData ? (
        <Alert
          type="error"
          showIcon
          icon={<ExclamationCircleOutlined />}
          style={{ marginTop: 16 }}
          message="Восстановить эти данные будет неоткуда"
          description={
            <>
              Источники не отдают историю цен задним числом: удаленный снимок рынка
              утрачивается навсегда. Будет уничтожено снимков — {num(data?.snapshots)},
              расчетов — {num(data?.runs)}, предложений — {num(data?.offers)}.
            </>
          }
        />
      ) : null}
    </Modal>
  );
}
