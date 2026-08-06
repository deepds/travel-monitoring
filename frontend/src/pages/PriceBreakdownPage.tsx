/**
 * Разбор цены: почему получилась эта цифра.
 *
 * Открывается кликом по любой цене на витрине. Четыре блока в порядке
 * вопросов, которые задает человек: денежная воронка, «что если», состав
 * выборки, сверка с источником.
 *
 * ДЕНЕЖНАЯ ВОРОНКА — главный элемент страницы. Не счетчики отброшенного, а
 * то, как менялась бы цифра на каждом шаге отбора. Видна цена каждого правила
 * в рублях: ровно эта работа до сих пор делалась запросами к базе руками, и
 * половина найденных за неделю дефектов была невидима именно потому, что
 * смотреть было некуда.
 *
 * «ЧТО ЕСЛИ» пересчитывает цифру без выбранных правил. Это выполнимо, потому
 * что предложения не удаляются: каждое хранится с пометкой, почему отброшено.
 * Обращений к источникам нет, результат помечен предварительным и официальную
 * цифру не подменяет — методика меняется только новой версией профиля.
 */

import { Alert, Card, Checkbox, Col, Empty, Row, Space, Statistic, Table, Tabs, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api } from '@/api/client';
import type { FunnelStep } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { OffersTable } from '@/components/OffersTable';
import { RailComparisonTable } from '@/components/RailComparisonTable';
import { useAsync } from '@/hooks/useAsync';
import { money, num } from '@/utils/format';

const { Text, Paragraph } = Typography;

/** Пустая страница осмысленна: сюда приходят по ссылке с витрины. */
function NoRunSelected() {
  return (
    <Card size="small">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={4}>
            <span>Разбор открывается от конкретной цены.</span>
            <Link to="/">Выбрать цену на витрине →</Link>
          </Space>
        }
      />
    </Card>
  );
}

function FunnelTable({ steps }: { steps: FunnelStep[] }) {
  return (
    <Table<FunnelStep>
      size="small"
      rowKey="step"
      pagination={false}
      dataSource={steps}
      scroll={{ x: 620 }}
      columns={[
        {
          title: 'Шаг отбора',
          dataIndex: 'title',
          render: (value: string, row) => (
            <Space direction="vertical" size={0}>
              <span>{value}</span>
              {Object.keys(row.reasons ?? {}).length ? (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {Object.entries(row.reasons)
                    .map(([reason, count]) => `${reason} — ${count}`)
                    .join('; ')}
                </Text>
              ) : null}
            </Space>
          ),
        },
        {
          title: 'Предложений',
          dataIndex: 'count',
          align: 'right',
          width: 120,
          render: (value: number, row) => (
            <Space size={4}>
              <span>{num(value)}</span>
              {row.removed ? (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  −{row.removed}
                </Text>
              ) : null}
            </Space>
          ),
        },
        {
          title: 'Медиана',
          dataIndex: 'median',
          align: 'right',
          width: 160,
          render: (value: number | null, row) => (
            <Space direction="vertical" size={0} align="end">
              <span>{value === null ? '—' : money(value)}</span>
              {/* Цена правила в рублях — то, ради чего таблица и построена. */}
              {row.median_delta ? (
                <Text type={row.median_delta > 0 ? 'danger' : 'success'} style={{ fontSize: 11 }}>
                  {row.median_delta > 0 ? '+' : ''}
                  {money(row.median_delta)}
                </Text>
              ) : null}
            </Space>
          ),
        },
        {
          title: 'Размах',
          key: 'range',
          align: 'right',
          width: 200,
          render: (_: unknown, row) =>
            row.min === null ? (
              '—'
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {money(row.min)} … {money(row.max ?? 0)}
              </Text>
            ),
        },
      ]}
    />
  );
}

export default function PriceBreakdownPage() {
  const { id = '' } = useParams();
  const [ignored, setIgnored] = useState<string[]>([]);

  // Загрузка объявлена до проверки идентификатора: порядок хуков в React
  // обязан совпадать между отрисовками, а ранний возврат его меняет. Пустой
  // идентификатор просто не запускает запрос.
  const run = useAsync(() => (id ? api.run(id) : Promise.resolve(null)), [id]);
  const funnel = useAsync(() => (id ? api.runFunnel(id) : Promise.resolve(null)), [id]);
  const whatIf = useAsync(
    () =>
      id
        ? api.runWhatIf(id, ignored.length ? { ignore_filter: ignored } : undefined)
        : Promise.resolve(null),
    [id, ignored.join(',')],
  );

  const steps = funnel.data?.steps ?? [];
  const finalStep = steps.length ? steps[steps.length - 1] : null;
  const switches = whatIf.data?.switches ?? [];

  const scenarioTitle = useMemo(() => {
    const data = run.data as any;
    if (!data) return undefined;
    return `${data.scenario_code ?? ''} · ${data.scenario_name ?? ''}`.trim();
  }, [run.data]);

  if (!id) return <NoRunSelected />;

  return (
    <>
      <PageTitle
        title="Разбор цены"
        subtitle={scenarioTitle}
        extra={<Link to="/">← К витрине</Link>}
      />

      <Tabs
        items={[
          {
            key: 'funnel',
            label: 'Почему такая цифра',
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <AsyncBlock loading={funnel.loading} error={funnel.error} minHeight={200}>
                  {funnel.data?.available === false ? (
                    <Alert type="info" showIcon message={funnel.data.reason} />
                  ) : (
                    <Card
                      size="small"
                      title="Денежная воронка"
                      extra={
                        finalStep ? (
                          <Text type="secondary">
                            В расчет вошло {num(finalStep.count)} предложений
                          </Text>
                        ) : null
                      }
                    >
                      <Paragraph type="secondary" style={{ fontSize: 12 }}>
                        Каждая строка — шаг отбора. Справа видно, во сколько рублей
                        обошлось правило и до какого размера оно сузило выборку.
                      </Paragraph>
                      <FunnelTable steps={steps} />
                    </Card>
                  )}
                </AsyncBlock>

                <Card size="small" title="Что если считать иначе">
                  <Paragraph type="secondary" style={{ fontSize: 12 }}>
                    Пересчет идет по уже собранным предложениям — к источникам
                    обращений нет. Результат предварительный: официальная цифра
                    считается действующей методикой и не меняется.
                  </Paragraph>
                  {switches.length === 0 ? (
                    <Text type="secondary">
                      В этом наблюдении правила методики ничего не отсекли — менять нечего.
                    </Text>
                  ) : (
                    <>
                      <Checkbox.Group
                        value={ignored}
                        onChange={(value) => setIgnored(value as string[])}
                      >
                        <Space direction="vertical" size={4}>
                          {switches
                            .filter((item) => item.kind === 'filter_reason')
                            .map((item) => (
                              <Checkbox key={item.code} value={item.code}>
                                Показать без правила: {item.title}{' '}
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  (вернется {item.offers} предл.)
                                </Text>
                              </Checkbox>
                            ))}
                        </Space>
                      </Checkbox.Group>

                      <Row gutter={16} style={{ marginTop: 16 }}>
                        <Col xs={12} md={8}>
                          <Statistic
                            title="Официальная медиана"
                            value={whatIf.data?.official?.median ?? 0}
                            formatter={(value) => money(Number(value))}
                          />
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {num(whatIf.data?.official?.count ?? 0)} предложений
                          </Text>
                        </Col>
                        <Col xs={12} md={8}>
                          <Statistic
                            title="Предварительный пересчет"
                            value={whatIf.data?.revised?.median ?? 0}
                            formatter={(value) => money(Number(value))}
                          />
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {num(whatIf.data?.revised?.count ?? 0)} предложений
                          </Text>
                        </Col>
                        <Col xs={24} md={8}>
                          {whatIf.data?.median_delta ? (
                            <Statistic
                              title="Разница"
                              value={whatIf.data.median_delta}
                              formatter={(value) => money(Number(value))}
                              valueStyle={{
                                color:
                                  whatIf.data.median_delta > 0 ? '#cf1322' : '#3f8600',
                              }}
                            />
                          ) : (
                            <Text type="secondary">Правила не выбраны</Text>
                          )}
                        </Col>
                      </Row>
                    </>
                  )}
                </Card>

                <Card size="small" title="Сверка с источником">
                  <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
                    Сайт источника крупно показывает минимум («от»), мы —
                    медиану. Пока эти числа не стоят рядом, разница читается как
                    ошибка: по Казани минимум 1 260 ₽ при медиане около 6 900.
                  </Paragraph>
                  {finalStep ? (
                    <Row gutter={16}>
                      <Col xs={12} md={6}>
                        <Statistic
                          title="Минимум («от»)"
                          value={finalStep.min ?? 0}
                          formatter={(value) => money(Number(value))}
                        />
                      </Col>
                      <Col xs={12} md={6}>
                        <Statistic
                          title="Медиана («типично»)"
                          value={finalStep.median ?? 0}
                          formatter={(value) => money(Number(value))}
                        />
                      </Col>
                      <Col xs={12} md={6}>
                        <Statistic
                          title="Четверть дешевле"
                          value={finalStep.p25 ?? 0}
                          formatter={(value) => money(Number(value))}
                        />
                      </Col>
                      <Col xs={12} md={6}>
                        <Statistic
                          title="Максимум"
                          value={finalStep.max ?? 0}
                          formatter={(value) => money(Number(value))}
                        />
                      </Col>
                    </Row>
                  ) : null}
                </Card>
              </Space>
            ),
          },
          {
            key: 'offers',
            label: 'Первоисточник',
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Alert
                  type="info"
                  showIcon
                  message="Все собранное по этому наблюдению"
                  description="Каждое предложение — с ценой, условиями и пометкой, вошло ли оно в расчет и почему нет. Ссылка ведет на ту же выдачу у источника: цену можно проверить руками."
                />
                <OffersTable runId={id} />
                <Card size="small" title="Один поезд у двух источников">
                  <RailComparisonTable runId={id} />
                </Card>
              </Space>
            ),
          },
        ]}
      />
    </>
  );
}
