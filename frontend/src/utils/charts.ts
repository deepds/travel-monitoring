/** Общие элементы графиков динамики цены. */

/** Цвет ряда «Итого». Совпадает с colorPrimary темы, задан явно ради коридора. */
export const TOTAL_COLOR = '#1668dc';

/**
 * Коридор размаха вокруг основного ряда.
 *
 * Рисуется двумя пунктирными линиями того же цвета, что и медиана, без заливки
 * и без записи в легенде: это не самостоятельные ряды, а границы наблюдавшихся
 * цен вокруг основного показателя.
 *
 * Ряды названы «минимум» и «максимум», а не «доверительный интервал»: это
 * фактический размах выборки, а не интервальная оценка — вероятностного смысла
 * у этих границ нет.
 */
export function rangeSeries(
  low: (number | null | undefined)[],
  high: (number | null | undefined)[],
  color: string = TOTAL_COLOR,
) {
  const hasData = [...low, ...high].some((value) => value !== null && value !== undefined);
  if (!hasData) return [];

  const style = {
    type: 'line' as const,
    smooth: true,
    symbol: 'none' as const,
    silent: false,
    lineStyle: { width: 1, type: 'dashed' as const, color, opacity: 0.7 },
    itemStyle: { color },
    // Ряд не попадает в легенду: соответствующего имени нет в legend.data.
    emphasis: { disabled: true },
  };

  return [
    { ...style, name: 'минимум', data: low },
    { ...style, name: 'максимум', data: high },
  ];
}
