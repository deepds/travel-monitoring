import {
  ApiOutlined, AreaChartOutlined, AuditOutlined, CalculatorOutlined,
  CameraOutlined, DashboardOutlined, DatabaseOutlined, DownloadOutlined,
  ExperimentOutlined, LogoutOutlined, SettingOutlined, ThunderboltOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { Alert, Layout, Menu, Spin, Tag, Typography } from 'antd';
import { useMemo } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';
import { ENVIRONMENT_LABEL, USER_ROLE_LABEL, labelOf } from '@/utils/format';
import AuditPage from '@/pages/AuditPage';
import ConstructorPage from '@/pages/ConstructorPage';
import DashboardPage from '@/pages/DashboardPage';
import ExportsPage from '@/pages/ExportsPage';
import JobsPage from '@/pages/JobsPage';
import LoginPage from '@/pages/LoginPage';
import ProfilesPage from '@/pages/ProfilesPage';
import RunDetailPage from '@/pages/RunDetailPage';
import RunsPage from '@/pages/RunsPage';
import ScenarioDetailPage from '@/pages/ScenarioDetailPage';
import ScenariosPage from '@/pages/ScenariosPage';
import SnapshotDetailPage from '@/pages/SnapshotDetailPage';
import SnapshotsPage from '@/pages/SnapshotsPage';
import SourcesPage from '@/pages/SourcesPage';
import AdminPage from '@/pages/AdminPage';

const { Header, Content, Sider } = Layout;
const { Text } = Typography;

interface NavItem {
  key: string;
  icon: JSX.Element;
  label: string;
  minRole: 'VIEWER' | 'ANALYST' | 'ADMIN';
}

const NAV: NavItem[] = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: 'Дашборд', minRole: 'VIEWER' },
  { key: '/constructor', icon: <CalculatorOutlined />, label: 'Расчет сценария', minRole: 'ANALYST' },
  { key: '/scenarios', icon: <UnorderedListOutlined />, label: 'Сценарии', minRole: 'VIEWER' },
  { key: '/runs', icon: <AreaChartOutlined />, label: 'Расчеты', minRole: 'VIEWER' },
  { key: '/snapshots', icon: <CameraOutlined />, label: 'Снимки рынка', minRole: 'VIEWER' },
  { key: '/sources', icon: <ApiOutlined />, label: 'Источники', minRole: 'VIEWER' },
  { key: '/profiles', icon: <ExperimentOutlined />, label: 'Профили расчета', minRole: 'VIEWER' },
  { key: '/jobs', icon: <ThunderboltOutlined />, label: 'Задачи', minRole: 'VIEWER' },
  { key: '/exports', icon: <DownloadOutlined />, label: 'Экспорт', minRole: 'ANALYST' },
  { key: '/admin', icon: <SettingOutlined />, label: 'Администрирование', minRole: 'ADMIN' },
  { key: '/audit', icon: <AuditOutlined />, label: 'Аудит', minRole: 'ADMIN' },
];

export default function App() {
  const { principal, version, loading, logout, can } = useAuth();
  const location = useLocation();

  const items = useMemo(
    () =>
      NAV.filter((item) => can(item.minRole)).map((item) => ({
        key: item.key,
        icon: item.icon,
        label: <Link to={item.key}>{item.label}</Link>,
      })),
    [can],
  );

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
        <Spin size="large" tip="Загрузка..." />
      </div>
    );
  }

  if (!principal) return <LoginPage />;

  const selected = NAV.map((item) => item.key)
    .filter((key) => location.pathname.startsWith(key))
    .sort((a, b) => b.length - a.length)
    .slice(0, 1);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth="0" width={230} theme="dark">
        <div style={{ padding: '18px 16px', color: '#fff' }}>
          <div style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.3 }}>
            <DatabaseOutlined /> Мониторинг
          </div>
          <div style={{ fontSize: 12, opacity: 0.65 }}>стоимости путешествий</div>
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={selected} items={items} />
      </Sider>

      <Layout>
        <Header
          style={{
            background: '#fff', display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', padding: '0 24px', gap: 16,
          }}
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {version?.deployment_mode === 'OPEN' ? (
              <Tag color="red">Открытый режим — авторизация отключена</Tag>
            ) : null}
            {version?.sandbox_sources_enabled ? (
              <Tag color="purple" icon={<ExperimentOutlined />}>
                Песочница включена
              </Tag>
            ) : null}
            {version?.environment && version.environment !== 'prod' ? (
              <Tag>{labelOf(ENVIRONMENT_LABEL, version.environment)}</Tag>
            ) : null}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Text type="secondary">
              {principal.display_name ?? principal.username}
            </Text>
            <Tag color={principal.role === 'ADMIN' ? 'gold' : principal.role === 'ANALYST' ? 'blue' : 'default'}>
              {labelOf(USER_ROLE_LABEL, principal.role)}
            </Tag>
            <a onClick={logout} title="Выйти">
              <LogoutOutlined />
            </a>
          </div>
        </Header>

        <Content>
          <div className="tco-content">
            {version?.deployment_mode === 'OPEN' ? (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="Развертывание без авторизации"
                description="Это временный режим. Он должен быть явно отражен в ограничениях эксплуатации."
              />
            ) : null}

            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/constructor" element={<ConstructorPage />} />
              <Route path="/scenarios" element={<ScenariosPage />} />
              <Route path="/scenarios/:id" element={<ScenarioDetailPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="/runs/:id" element={<RunDetailPage />} />
              <Route path="/snapshots" element={<SnapshotsPage />} />
              <Route path="/snapshots/:id" element={<SnapshotDetailPage />} />
              <Route path="/sources" element={<SourcesPage />} />
              <Route path="/profiles" element={<ProfilesPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/exports" element={<ExportsPage />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
