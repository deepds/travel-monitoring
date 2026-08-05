/**
 * Тема интерфейса: светлая, темная или системная.
 *
 * Выбор сохраняется между сессиями. Режим «системная» не запоминает конкретный
 * цвет, а следит за настройкой операционной системы и переключается вместе с
 * ней — иначе вечером у пользователя оставался бы дневной интерфейс.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

const STORAGE_KEY = 'tco.theme';

interface ThemeState {
  /** Что выбрал пользователь. */
  mode: ThemeMode;
  /** Что показано на самом деле: «системная» уже раскрыта в конкретный цвет. */
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

const systemTheme = (): ResolvedTheme =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';

const storedMode = (): ThemeMode => {
  if (typeof window === 'undefined') return 'system';
  const saved = window.localStorage.getItem(STORAGE_KEY);
  return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'system';
};

/** Класс на корневом элементе — по нему переключаются CSS-переменные. */
function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(resolved);
  root.style.colorScheme = resolved;

  // Мобильные браузеры красят строку состояния по этому значению.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', resolved === 'dark' ? '#0a0f1a' : '#f5f7fa');
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(storedMode);
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    storedMode() === 'system' ? systemTheme() : (storedMode() as ResolvedTheme),
  );

  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  // В системном режиме следим за настройкой ОС; в ручном — нет.
  useEffect(() => {
    if (mode !== 'system') return undefined;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => setResolved(event.matches ? 'dark' : 'light');
    query.addEventListener('change', onChange);
    setResolved(query.matches ? 'dark' : 'light');
    return () => query.removeEventListener('change', onChange);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    if (next !== 'system') setResolved(next);
  }, []);

  /** Переключение всегда уводит в ручной режим: пользователь выбрал явно. */
  const toggle = useCallback(() => {
    setMode(resolved === 'dark' ? 'light' : 'dark');
  }, [resolved, setMode]);

  const value = useMemo(
    () => ({ mode, resolved, setMode, toggle }),
    [mode, resolved, setMode, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme вызван вне ThemeProvider');
  return context;
}
