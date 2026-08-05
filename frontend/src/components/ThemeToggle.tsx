/**
 * Переключатель темы.
 *
 * Клик по кнопке сразу меняет цвет — это самое частое действие. Выпадающий
 * список нужен, чтобы вернуться к системной настройке: иначе однажды нажатая
 * кнопка навсегда отвязала бы интерфейс от настроек операционной системы.
 */

import { BulbOutlined, DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { Button, Dropdown, Tooltip } from 'antd';
import type { MenuProps } from 'antd';

import { useTheme } from '@/theme/ThemeContext';
import type { ThemeMode } from '@/theme/ThemeContext';

const LABEL: Record<ThemeMode, string> = {
  light: 'Светлая',
  dark: 'Темная',
  system: 'Как в системе',
};

export function ThemeToggle() {
  const { mode, resolved, setMode, toggle } = useTheme();

  const items: MenuProps['items'] = [
    { key: 'light', icon: <SunOutlined />, label: LABEL.light },
    { key: 'dark', icon: <MoonOutlined />, label: LABEL.dark },
    { type: 'divider' },
    { key: 'system', icon: <DesktopOutlined />, label: LABEL.system },
  ];

  return (
    <Dropdown
      menu={{
        items,
        selectable: true,
        selectedKeys: [mode],
        onClick: ({ key }) => setMode(key as ThemeMode),
      }}
      trigger={['contextMenu']}
      placement="bottomRight"
    >
      <Tooltip
        title={
          mode === 'system'
            ? `${LABEL.system} · сейчас ${resolved === 'dark' ? 'темная' : 'светлая'}`
            : LABEL[mode]
        }
      >
        <Button
          type="text"
          aria-label="Переключить тему"
          icon={
            mode === 'system' ? (
              <BulbOutlined />
            ) : resolved === 'dark' ? (
              <MoonOutlined />
            ) : (
              <SunOutlined />
            )
          }
          onClick={toggle}
        />
      </Tooltip>
    </Dropdown>
  );
}
