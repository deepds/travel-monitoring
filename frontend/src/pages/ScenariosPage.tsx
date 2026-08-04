import { PlusOutlined } from '@ant-design/icons';
import { App, Button, Card, Input, Select, Space, Switch, Table, Tag, Tooltip } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
import type { ScenarioBrief } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { ExportButton } from '@/components/ExportButton';
import { ScenarioCreateModal, ScenarioDeleteModal } from '@/components/ScenarioDialogs';
import { useAuth } from '@/auth/AuthContext';
import { useAsync } from '@/hooks/useAsync';
import { ACCOMMODATION_LABEL, TRANSPORT_LABEL, dateOnly, starsLabel } from '@/utils/format';

export default function ScenariosPage() {
  const { message } = App.useApp();
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState('');
  const [origin, setOrigin] = useState<string>();
  const [destination, setDestination] = useState<string>();
  const [transport, setTransport] = useState<string>();
  const [accommodation, setAccommodation] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<ScenarioBrief | null>(null);

  const cities = useAsync(() => api.cities(), []);
  const query = useMemo(
    () => ({
      page,
      page_size: pageSize,
      search: search || undefined,
      origin,
      destination,
      transport_type: transport,
      accommodation_type: accommodation,
    }),
    [page, pageSize, search, origin, destination, transport, accommodation],
  );

  const scenarios = useAsync(() => api.scenarios(query), [JSON.stringify(query)]);

  const toggleActive = async (row: ScenarioBrief) => {
    try {
      if (row.is_active) await api.deactivateScenario(row.id);
      else await api.activateScenario(row.id);
      message.success(row.is_active ? 'Сценарий деактивирован' : 'Сценарий активирован');
      scenarios.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось изменить сценарий');
    }
  };

  const cityOptions = (cities.data?.items ?? []).map((c) => ({ value: c.code, label: c.name }));

  return (
    <>
      <PageTitle
        title="Сценарии наблюдения"
        subtitle="Каждая уникальная комбинация маршрута, дат и состава — отдельный сценарий"
        extra={
          <Space wrap>
            {/*
              Набора данных «Сценарии» в API нет, поэтому выгружаются расчеты по
              текущим направлениям — это и написано на кнопке, чтобы результат
              не расходился с ожиданием.
            */}
            <ExportButton
              dataset="SCENARIO_RUNS"
              filters={{ origin, destination, transport_type: transport }}
              label="Выгрузить расчеты"
            />
            {can('ADMIN') ? (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                Создать сценарий
              </Button>
            ) : null}
          </Space>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="Код или название"
            style={{ width: 260 }}
            onSearch={(value) => {
              setSearch(value);
              setPage(1);
            }}
          />
          <Select allowClear placeholder="Откуда" style={{ width: 160 }} options={cityOptions}
            value={origin} onChange={(v) => { setOrigin(v); setPage(1); }} />
          <Select allowClear placeholder="Куда" style={{ width: 160 }} options={cityOptions}
            value={destination} onChange={(v) => { setDestination(v); setPage(1); }} />
          <Select allowClear placeholder="Транспорт" style={{ width: 130 }}
            options={[{ value: 'AVIA', label: 'Авиа' }, { value: 'RAIL', label: 'ЖД' }]}
            value={transport} onChange={(v) => { setTransport(v); setPage(1); }} />
          <Select allowClear placeholder="Размещение" style={{ width: 160 }}
            options={Object.entries(ACCOMMODATION_LABEL).map(([value, label]) => ({ value, label }))}
            value={accommodation} onChange={(v) => { setAccommodation(v); setPage(1); }} />
        </Space>
      </Card>

      <Card size="small">
        <AsyncBlock loading={scenarios.loading} error={scenarios.error}>
          <Table<ScenarioBrief>
            size="small"
            rowKey="id"
            dataSource={scenarios.data?.items ?? []}
            scroll={{ x: 1100 }}
            pagination={{
              current: page,
              pageSize,
              total: scenarios.data?.meta.total ?? 0,
              showSizeChanger: true,
              showTotal: (total) => `всего ${total}`,
              onChange: (nextPage, nextSize) => {
                setPage(nextPage);
                setPageSize(nextSize);
              },
            }}
            columns={[
              {
                title: 'Код',
                dataIndex: 'code',
                fixed: 'left',
                width: 260,
                render: (value: string, row) => (
                  <Link to={`/scenarios/${row.id}`} className="tco-monospace">
                    {value}
                  </Link>
                ),
              },
              {
                title: 'Маршрут',
                key: 'route',
                render: (_, row) => (
                  <span>
                    {row.origin?.name} → {row.destination?.name}
                  </span>
                ),
              },
              {
                title: 'Даты',
                key: 'dates',
                render: (_, row) => (
                  <span>
                    {dateOnly(row.departure_date)} — {dateOnly(row.return_date)}
                    <br />
                    <span style={{ color: '#8c8c8c', fontSize: 12 }}>{row.nights} ноч.</span>
                  </span>
                ),
              },
              {
                title: 'Транспорт',
                dataIndex: 'transport_type',
                render: (value: string | null) =>
                  value ? (
                    <Tag>{TRANSPORT_LABEL[value] ?? value}</Tag>
                  ) : (
                    <Tag color="default">не наблюдается</Tag>
                  ),
              },
              {
                title: 'Размещение',
                key: 'accommodation',
                render: (_, row) =>
                  row.accommodation_type ? (
                    <span>
                      {ACCOMMODATION_LABEL[row.accommodation_type] ?? row.accommodation_type}
                      <br />
                      <span style={{ color: '#8c8c8c', fontSize: 12 }}>{starsLabel(row.stars)}</span>
                    </span>
                  ) : (
                    <Tag color="default">не наблюдается</Tag>
                  ),
              },
              {
                title: 'Статус',
                dataIndex: 'is_active',
                render: (value: boolean) =>
                  value ? <Tag color="success">активен</Tag> : <Tag>выключен</Tag>,
              },
              ...(can('ADMIN')
                ? [
                    {
                      title: 'Действия',
                      key: 'actions',
                      fixed: 'right' as const,
                      width: 190,
                      render: (_: unknown, row: ScenarioBrief) => (
                        <Space size="small">
                          <Tooltip
                            title={
                              row.is_active
                                ? 'Снять с планового наблюдения'
                                : 'Поставить на плановое наблюдение'
                            }
                          >
                            <Switch
                              size="small"
                              checked={row.is_active}
                              onChange={() => toggleActive(row)}
                            />
                          </Tooltip>
                          <Button size="small" danger type="text" onClick={() => setToDelete(row)}>
                            Удалить
                          </Button>
                        </Space>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </AsyncBlock>
      </Card>

      <ScenarioCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => scenarios.reload()}
      />
      <ScenarioDeleteModal
        scenario={toDelete}
        onClose={() => setToDelete(null)}
        onDeleted={() => scenarios.reload()}
      />
    </>
  );
}
