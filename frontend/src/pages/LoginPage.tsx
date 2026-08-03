import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';

const { Title, Paragraph, Text } = Typography;

export default function LoginPage() {
  const { login, version } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    setError(null);
    try {
      await login(values.username, values.password);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Не удалось выполнить вход');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center',
        justifyContent: 'center', padding: 24,
      }}
    >
      <Card style={{ width: 420, boxShadow: '0 2px 16px rgba(0,0,0,0.08)' }}>
        <Title level={4} style={{ marginTop: 0 }}>
          Мониторинг стоимости путешествий
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 24 }}>
          Аналитическая платформа наблюдения за рынком транспорта и проживания.
        </Paragraph>

        {error ? (
          <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
        ) : null}

        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item
            name="username"
            label="Пользователь"
            rules={[{ required: true, message: 'Укажите имя пользователя' }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item
            name="password"
            label="Пароль"
            rules={[{ required: true, message: 'Укажите пароль' }]}
          >
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} size="large" block>
            Войти
          </Button>
        </Form>

        {version ? (
          <div style={{ marginTop: 20, textAlign: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Версия {version.versions.app} · среда {version.environment} · режим{' '}
              {version.deployment_mode}
            </Text>
            {version.sandbox_sources_enabled ? (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 12, textAlign: 'left' }}
                message="Включен синтетический источник"
                description="Часть данных стенда не является рыночной и помечается отдельно."
              />
            ) : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}
