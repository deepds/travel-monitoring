/** Общие элементы графиков динамики цены. */

import type { ResolvedTheme } from '@/theme/ThemeContext';

/** Цвет ряда «Итого». Совпадает с colorPrimary темы, задан явно ради коридора. */
export const TOTAL_COLOR = '#1668dc';

/**
 * Оформление графика под тему.
 *
 * ECharts не знает о CSS-переменных и по умолчанию рисует черный текст на
 * любом фоне, поэтому в темной теме подписи осей и легенда просто исчезали.
 * Значения совпадают с `styles/tokens.css`.
 */
export function chartTheme(resolved: ResolvedTheme) {
  const dark = resolved === 'dark';
  return {
    text: dark ? '#a8b5c8' : '#4a5568',
    textMuted: dark ? '#7a8ba8' : '#6b7280',
    axis: dark ? 'rgba(255, 255, 255, 0.18)' : '#e2e8f0',
    split: dark ? 'rgba(255, 255, 255, 0.08)' : '#eef2f7',
    tooltipBg: dark ? '#141b2d' : '#ffffff',
    tooltipBorder: dark ? 'rgba(255, 255, 255, 0.12)' : '#e2e8f0',
    accent: dark ? '#00a8ff' : '#005a9e',
    /** Палитра рядов: различима в обеих темах и не полагается на насыщенность. */
    palette: dark
      ? ['#00a8ff', '#ff7f2a', '#10b981', '#a78bfa', '#f59e0b']
      : ['#005a9e', '#b94d00', '#047857', '#6d28d9', '#b45309'],
  };
}

type AnyOption = Record<string, any>;

const axisTheme = (t: ReturnType<typeof chartTheme>, category: boolean) => ({
  axisLine: category ? { lineStyle: { color: t.axis } } : { show: false },
  axisTick: { lineStyle: { color: t.axis } },
  axisLabel: { color: t.textMuted },
  splitLine: category ? { show: false } : { lineStyle: { color: t.split } },
});

/**
 * Накладывает цвета темы на готовый option графика.
 *
 * Так оформление задается в одном месте: каждый график описывает только свои
 * данные, а оси, легенда и подсказка приходят из темы. Явно заданные в option
 * значения не перетираются — точечная настройка остается возможной.
 */
export function applyChartTheme(option: AnyOption, resolved: ResolvedTheme): AnyOption {
  const t = chartTheme(resolved);

  const withAxis = (axis: AnyOption | AnyOption[] | undefined, category: boolean) => {
    if (!axis) return axis;
    const base = axisTheme(t, category);
    return Array.isArray(axis)
      ? axis.map((item) => ({ ...base, ...item }))
      : { ...base, ...axis };
  };

  return {
    color: t.palette,
    ...option,
    textStyle: { fontFamily: "'Exo 2', 'Segoe UI', sans-serif", ...(option.textStyle ?? {}) },
    legend: option.legend
      ? { ...option.legend, textStyle: { color: t.text, ...(option.legend.textStyle ?? {}) } }
      : undefined,
    tooltip: option.tooltip
      ? {
          backgroundColor: t.tooltipBg,
          borderColor: t.tooltipBorder,
          textStyle: { color: resolved === 'dark' ? '#e8ecf4' : '#1a202c' },
          ...option.tooltip,
        }
      : undefined,
    xAxis: withAxis(option.xAxis, true),
    yAxis: withAxis(option.yAxis, false),
  };
}

/*
 * Коридора размаха (пунктирные «минимум» и «максимум» вокруг медианы) на
 * графиках динамики нет намеренно: границы читались как доверительный интервал
 * и сбивали с толку, а разброс уже показан колонкой P25–P75 в таблицах.
 */
