import { App, Alert, Button, Card, Drawer, Popconfirm, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { ApiError, api } from '@/api/client';
import type { Profile } from '@/api/types';
import { AsyncBlock, PageTitle } from '@/components/common';
import { useAuth } from '@/auth/AuthContext';
import { useAsync } from '@/hooks/useAsync';
import { PROFILE_STATUS_LABEL, dateTime, labelOf } from '@/utils/format';

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  DRAFT: 'default',
  ACTIVE: 'success',
  ARCHIVED: 'warning',
};

export default function ProfilesPage() {
  const { message } = App.useApp();
  const { can } = useAuth();
  const [selected, setSelected] = useState<Profile | null>(null);
  const profiles = useAsync(() => api.profiles(), []);

  const detail = useAsync(
    () => (selected ? api.profile(selected.id) : Promise.resolve(null)),
    [selected?.id],
  );

  const act = async (action: 'activate' | 'archive' | 'clone', row: Profile) => {
    try {
      if (action === 'activate') await api.activateProfile(row.id);
      if (action === 'archive') await api.archiveProfile(row.id);
      if (action === 'clone') await api.cloneProfile(row.id, {});
      message.success(
        action === 'activate' ? 'Профиль активирован'
          : action === 'archive' ? 'Профиль архивирован'
          : 'Создана новая версия-черновик',
      );
      profiles.reload();
    } catch (exc) {
      message.error(exc instanceof ApiError ? exc.message : 'Действие не выполнено');
    }
  };

  return (
    <>
      <PageTitle
        title="Профили расчета"
        subtitle="Версионируемая методика: черновик → активный → в архиве"
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Активная версия неизменяема"
        description="Изменение методики создает новую версию. Исторические расчеты не пересчитываются автоматически и сохраняют ссылку на свою версию профиля."
      />

      <Card size="small">
        <AsyncBlock loading={profiles.loading} error={profiles.error}>
          <Table<Profile>
            size="small"
            rowKey="id"
            dataSource={profiles.data?.items ?? []}
            pagination={false}
            scroll={{ x: 900 }}
            onRow={(row) => ({ onClick: () => setSelected(row), className: 'tco-clickable' })}
            columns={[
              {
                title: 'Профиль',
                dataIndex: 'label',
                render: (value: string, row) => (
                  <span>
                    <b>{value}</b>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {row.name}
                    </Text>
                  </span>
                ),
              },
              {
                title: 'Статус',
                dataIndex: 'status',
                render: (value: string) => (
                  <Tag color={STATUS_COLOR[value]}>{labelOf(PROFILE_STATUS_LABEL, value)}</Tag>
                ),
              },
              { title: 'Версия', dataIndex: 'version' },
              {
                title: 'Активирован',
                dataIndex: 'activated_at',
                render: (v: string | null) => dateTime(v),
              },
              {
                title: 'Архивирован',
                dataIndex: 'archived_at',
                render: (v: string | null) => dateTime(v),
              },
              ...(can('ADMIN')
                ? [
                    {
                      title: 'Действия',
                      key: 'actions',
                      fixed: 'right' as const,
                      width: 260,
                      render: (_: unknown, row: Profile) => (
                        <Space size="small" onClick={(e) => e.stopPropagation()}>
                          {row.status !== 'ACTIVE' && row.status !== 'ARCHIVED' ? (
                            <Popconfirm
                              title="Активировать профиль?"
                              description="Предыдущая активная версия будет архивирована."
                              onConfirm={() => act('activate', row)}
                              okText="Активировать"
                              cancelText="Отмена"
                            >
                              <Button size="small" type="link">
                                Активировать
                              </Button>
                            </Popconfirm>
                          ) : null}
                          {row.status !== 'ARCHIVED' ? (
                            <Button size="small" type="link" onClick={() => act('archive', row)}>
                              Архивировать
                            </Button>
                          ) : null}
                          <Button size="small" type="link" onClick={() => act('clone', row)}>
                            Клонировать
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

      <Drawer
        title={selected ? `Правила профиля ${selected.label}` : ''}
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={760}
      >
        <AsyncBlock loading={detail.loading} error={detail.error}>
          <Text type="secondary">{detail.data?.description}</Text>
          <pre
            className="tco-monospace"
            style={{ whiteSpace: 'pre-wrap', marginTop: 12, background: '#fafafa', padding: 12 }}
          >
            {JSON.stringify(detail.data?.rules ?? {}, null, 2)}
          </pre>
        </AsyncBlock>
      </Drawer>
    </>
  );
}
