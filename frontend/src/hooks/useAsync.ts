/** Хуки загрузки данных. */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/client';

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Загружает данные при монтировании и при изменении ``deps``.
 *
 * Результат устаревшего запроса отбрасывается: при быстрой смене фильтров
 * ответы могут прийти не в том порядке, в каком были отправлены.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const requestId = useRef(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const current = ++requestId.current;
    setLoading(true);
    setError(null);

    loaderRef
      .current()
      .then((result) => {
        if (current !== requestId.current) return;
        setData(result);
      })
      .catch((exc: unknown) => {
        if (current !== requestId.current) return;
        setError(exc instanceof ApiError ? exc.message : String(exc));
      })
      .finally(() => {
        if (current !== requestId.current) return;
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, loading, error, reload };
}

/** Периодический опрос: используется для наблюдения за фоновой задачей. */
export function usePolling(callback: () => void, intervalMs: number, active: boolean): void {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => saved.current(), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, active]);
}
