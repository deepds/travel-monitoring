/** Форматирование значений для интерфейса. */

import type { ConfidenceLevel, RunStatus } from '@/api/types';

const MONEY = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0,
});

const NUMBER = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

/** Итоговые суммы округляются: избыточная точность создает ложную уверенность. */
export const money = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : MONEY.format(value);

export const num = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : NUMBER.format(value);

export const percent = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)} %`;

export const score = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : value.toFixed(1);

export const dateTime = (value: string | null | undefined): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
};

export const dateOnly = (value: string | null | undefined): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('ru-RU');
};

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  SUCCESS: 'Успешно',
  PARTIAL_SUCCESS: 'Частично',
  FAILED: 'Ошибка',
  UNSUPPORTED: 'Не поддерживается',
  NO_DATA: 'Нет данных',
};

export const RUN_STATUS_COLOR: Record<RunStatus, string> = {
  SUCCESS: 'success',
  PARTIAL_SUCCESS: 'warning',
  FAILED: 'error',
  UNSUPPORTED: 'default',
  NO_DATA: 'default',
};

export const CONFIDENCE_LABEL: Record<ConfidenceLevel, string> = {
  HIGH: 'Высокая',
  MEDIUM: 'Средняя',
  LOW: 'Низкая',
  INSUFFICIENT: 'Недостаточно данных',
};

export const CONFIDENCE_COLOR: Record<ConfidenceLevel, string> = {
  HIGH: 'green',
  MEDIUM: 'gold',
  LOW: 'orange',
  INSUFFICIENT: 'red',
};

export const JOB_STATUS_COLOR: Record<string, string> = {
  PENDING: 'default',
  QUEUED: 'blue',
  RUNNING: 'processing',
  PARTIAL: 'warning',
  SUCCESS: 'success',
  FAILED: 'error',
  CANCELLED: 'default',
  RETRYING: 'warning',
  TIMED_OUT: 'error',
};

export const JOB_STATUS_LABEL: Record<string, string> = {
  PENDING: 'Создана',
  QUEUED: 'В очереди',
  RUNNING: 'Выполняется',
  PARTIAL: 'Частично',
  SUCCESS: 'Успешно',
  FAILED: 'Ошибка',
  CANCELLED: 'Отменена',
  RETRYING: 'Повтор',
  TIMED_OUT: 'Таймаут',
};

export const OUTCOME_COLOR: Record<string, string> = {
  SUCCESS: 'success',
  EMPTY: 'default',
  TIMEOUT: 'warning',
  RATE_LIMITED: 'warning',
  AUTH_ERROR: 'error',
  SCHEMA_ERROR: 'error',
  TRANSPORT_ERROR: 'error',
  UNSUPPORTED: 'default',
  DISABLED: 'default',
  CIRCUIT_OPEN: 'error',
};

export const TRANSPORT_LABEL: Record<string, string> = {
  AVIA: 'Авиа',
  RAIL: 'ЖД',
};

export const ACCOMMODATION_LABEL: Record<string, string> = {
  HOTEL: 'Гостиница',
  APARTMENT: 'Апартаменты',
  GUEST_HOUSE: 'Гостевой дом',
  HOSTEL: 'Хостел',
  SANATORIUM: 'Санаторий',
  OTHER: 'Иное',
};

/** Знак изменения для отображения динамики. */
export const changeTone = (value: number | null | undefined): 'up' | 'down' | 'flat' => {
  if (value === null || value === undefined || Math.abs(value) < 0.0005) return 'flat';
  return value > 0 ? 'up' : 'down';
};

export const signedPercent = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(1)} %`;
};

export const starsLabel = (value: string | null | undefined): string => {
  if (!value) return '—';
  if (value === 'ANY') return 'Любая';
  if (value === 'UNRATED') return 'Без звезд';
  if (value === 'NOT_APPLICABLE') return 'Неприменимо';
  return `${value}★`;
};
