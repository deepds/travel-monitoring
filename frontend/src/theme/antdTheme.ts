/**
 * Отображение токенов темы в Ant Design.
 *
 * Значения дублируют `styles/tokens.css` намеренно: Ant Design считает часть
 * производных цветов в JavaScript и не умеет читать CSS-переменные. Правки
 * нужно вносить в обоих местах — расхождение сразу видно на границе карточек.
 */

import { theme } from 'antd';
import type { ThemeConfig } from 'antd';

import type { ResolvedTheme } from '@/theme/ThemeContext';

const FONT_BODY =
  "'Exo 2', 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif";

const light = {
  bg: '#f5f7fa',
  card: '#ffffff',
  sidebar: '#ffffff',
  text: '#1a202c',
  textSecondary: '#4a5568',
  textMuted: '#6b7280',
  border: '#e2e8f0',
  accent: '#005a9e',
  success: '#047857',
  warning: '#b45309',
  error: '#b91c1c',
  info: '#1d4ed8',
};

const dark = {
  bg: '#0a0f1a',
  card: '#141b2d',
  sidebar: '#0d121c',
  text: '#e8ecf4',
  textSecondary: '#a8b5c8',
  textMuted: '#7a8ba8',
  border: 'rgba(255, 255, 255, 0.12)',
  accent: '#00a8ff',
  success: '#10b981',
  warning: '#f59e0b',
  error: '#ef4444',
  info: '#3b82f6',
};

export function antdTheme(resolved: ResolvedTheme): ThemeConfig {
  const p = resolved === 'dark' ? dark : light;

  return {
    algorithm: resolved === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: p.accent,
      colorInfo: p.info,
      colorSuccess: p.success,
      colorWarning: p.warning,
      colorError: p.error,

      colorBgLayout: p.bg,
      colorBgContainer: p.card,
      colorBgElevated: p.card,

      colorText: p.text,
      colorTextSecondary: p.textSecondary,
      colorTextTertiary: p.textMuted,

      colorBorder: p.border,
      colorBorderSecondary: p.border,

      borderRadius: 8,
      borderRadiusLG: 12,
      fontFamily: FONT_BODY,
      fontSize: 14,
      // Тень карточек мягче стандартной: на дашборде их много, и резкие
      // границы дробят экран.
      boxShadow:
        resolved === 'dark'
          ? '0 2px 8px rgba(0, 0, 0, 0.3)'
          : '0 2px 8px rgba(0, 0, 0, 0.08)',
    },
    components: {
      Layout: {
        siderBg: p.sidebar,
        headerBg: p.card,
        bodyBg: p.bg,
        headerHeight: 56,
      },
      Menu: {
        darkItemBg: p.sidebar,
        darkSubMenuItemBg: p.sidebar,
        itemBg: p.sidebar,
        // Выделение пункта — заливкой акцента, а не только цветом текста:
        // на длинном списке разделов так заметно быстрее.
        itemSelectedBg: resolved === 'dark' ? 'rgba(0, 168, 255, 0.16)' : 'rgba(0, 90, 158, 0.1)',
        itemSelectedColor: p.accent,
        itemHoverBg: resolved === 'dark' ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.04)',
        itemBorderRadius: 8,
      },
      Card: { headerFontSize: 15 },
      Table: {
        headerBg: resolved === 'dark' ? '#0f1520' : '#f8fafc',
        headerColor: p.textSecondary,
        rowHoverBg: resolved === 'dark' ? 'rgba(255, 255, 255, 0.04)' : '#f8fafc',
      },
      Statistic: { contentFontSize: 22 },
    },
  };
}
