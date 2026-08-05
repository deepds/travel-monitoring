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

/**
 * Дата с двузначным годом — для подписей осей.
 *
 * На горизонтальной оси десяток полных дат сливается в сплошную полосу, а год
 * все же нужен: наблюдаемые даты вылета переходят через новый год.
 */
export const dateAxis = (value: string | null | undefined): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });
};

/**
 * Подпись значения из словаря.
 *
 * Незнакомое значение показывается как есть: интерфейс не должен молча прятать
 * то, что появилось в API, но еще не переведено.
 */
export const labelOf = (
  dictionary: Record<string, string>,
  value: string | null | undefined,
): string => (value ? (dictionary[value] ?? value) : '—');

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

export const OUTCOME_LABEL: Record<string, string> = {
  SUCCESS: 'Успех',
  EMPTY: 'Пусто',
  TIMEOUT: 'Таймаут',
  RATE_LIMITED: 'Ограничение темпа',
  AUTH_ERROR: 'Ошибка авторизации',
  SCHEMA_ERROR: 'Ошибка схемы',
  TRANSPORT_ERROR: 'Ошибка соединения',
  UNSUPPORTED: 'Не поддерживается',
  DISABLED: 'Выключен',
  CIRCUIT_OPEN: 'Предохранитель разомкнут',
};

export const JOB_TYPE_LABEL: Record<string, string> = {
  ON_DEMAND_CALCULATION: 'Расчет по запросу',
  MONITORING_SCENARIO: 'Мониторинг сценария',
  MONITORING_BATCH: 'Пакет мониторинга',
  SNAPSHOT_REPLAY: 'Пересчет снимка',
  SCENARIO_IMPORT: 'Импорт сценариев',
  EXPORT: 'Экспорт',
  SOURCE_HEALTH_CHECK: 'Проверка источника',
  SOURCE_CONFIDENCE: 'Пересчет доверия к источнику',
  MAINTENANCE: 'Обслуживание',
};

export const SCENARIO_TYPE_LABEL: Record<string, string> = {
  MONITORING: 'Мониторинг',
  ON_DEMAND: 'По запросу',
  TEMPLATE: 'Шаблон',
};

export const RUN_TYPE_LABEL: Record<string, string> = {
  MONITORING: 'Мониторинг',
  ON_DEMAND: 'По запросу',
  REPLAY: 'Пересчет',
  MANUAL: 'Ручной',
};

export const SNAPSHOT_TYPE_LABEL: Record<string, string> = {
  DAILY_MONITORING: 'Плановый мониторинг',
  ON_DEMAND: 'По запросу',
  MANUAL_RETRY: 'Ручной повтор',
  METHODOLOGY_REPLAY: 'Пересчет методики',
};

export const SNAPSHOT_STATUS_LABEL: Record<string, string> = {
  COLLECTING: 'Собирается',
  COMPLETE: 'Полный',
  PARTIAL: 'Частичный',
  EMPTY: 'Пустой',
  FAILED: 'Ошибка',
};

export const PROFILE_STATUS_LABEL: Record<string, string> = {
  DRAFT: 'Черновик',
  ACTIVE: 'Активный',
  ARCHIVED: 'В архиве',
};

/** Статус квалификации источника (SCOPE-R E §1). */
export const QUALIFICATION_LABEL: Record<string, string> = {
  APPROVED: 'Допущен',
  CONDITIONAL: 'Допущен условно',
  REJECTED: 'Отклонен',
  CANDIDATE: 'Кандидат',
};

/** REST, MCP и HTML — общепринятые сокращения, они не переводятся. */
export const PROTOCOL_LABEL: Record<string, string> = {
  REST: 'REST',
  MCP: 'MCP',
  HTML: 'HTML',
  SYNTHETIC: 'Синтетический',
};

export const SOURCE_CONFIDENCE_LABEL: Record<string, string> = {
  HIGH: 'Высокое',
  MEDIUM: 'Среднее',
  LOW: 'Низкое',
  UNTRUSTED: 'Нет доверия',
};

export const LEGAL_STATUS_LABEL: Record<string, string> = {
  PUBLIC_MCP_NO_AUTH: 'Публичный MCP без авторизации',
  PUBLIC_JSON_API: 'Публичный JSON API',
  PARTNER_OAUTH: 'Партнерский доступ (OAuth)',
  PARTNER_OAUTH2: 'Партнерский доступ (OAuth 2)',
  INTERNAL_SYNTHETIC: 'Внутренний синтетический',
};

export const COMPONENT_LABEL: Record<string, string> = {
  TRANSPORT: 'Транспорт',
  ACCOMMODATION: 'Проживание',
};

export const OFFER_TYPE_LABEL: Record<string, string> = {
  FLIGHT: 'Авиа',
  RAIL: 'ЖД',
  ACCOMMODATION: 'Проживание',
};

export const VALIDITY_LABEL: Record<string, string> = {
  VALID: 'Валидное',
  INVALID_PRICE: 'Некорректная цена',
  INVALID_CURRENCY: 'Некорректная валюта',
  INVALID_ROUTE: 'Некорректный маршрут',
  INVALID_DATES: 'Некорректные даты',
  INVALID_CAPACITY: 'Некорректная вместимость',
  INVALID_SCHEMA: 'Не соответствует схеме',
};

export const CLASSIFICATION_LABEL: Record<string, string> = {
  CLASSIFIED: 'Классифицировано',
  UNCLASSIFIED_FARE: 'Тариф не определен',
  UNCLASSIFIED_MEAL: 'Питание не определено',
  UNCLASSIFIED_CANCELLATION: 'Отмена не определена',
  UNCLASSIFIED_CAPACITY: 'Вместимость не определена',
};

export const EXCLUSION_LABEL: Record<string, string> = {
  NONE: 'Учтено',
  INVALID: 'Невалидное',
  TECHNICAL_DUPLICATE: 'Технический дубликат',
  PROFILE_FILTER: 'Отсеяно фильтром профиля',
  OUTLIER: 'Выброс',
  SOURCE_NOT_ELIGIBLE: 'Источник не допущен',
};

/** Причины отсева предложения фильтром профиля. */
export const FILTER_REASON_LABEL: Record<string, string> = {
  COMPONENT_NOT_OBSERVED: 'Компонента не наблюдается сценарием',
  TRANSPORT_TYPE_MISMATCH: 'Не тот вид транспорта',
  MISSING_FLIGHT_DETAIL: 'Нет деталей авиаперелета',
  BAGGAGE_UNCLASSIFIED: 'Багаж не классифицирован',
  BAGGAGE_MISMATCH: 'Багаж не соответствует тарифу',
  TOO_MANY_STOPS: 'Слишком много пересадок',
  MISSING_RAIL_DETAIL: 'Нет деталей ЖД-предложения',
  RAIL_CLASS_UNCLASSIFIED: 'Класс вагона не определен',
  RAIL_CLASS_MISMATCH: 'Класс вагона не соответствует',
  MISSING_ACCOMMODATION_DETAIL: 'Нет деталей проживания',
  ACCOMMODATION_TYPE_MISMATCH: 'Не тот тип размещения',
  STARS_MISMATCH: 'Звездность не соответствует',
  MEAL_MISMATCH: 'Питание не соответствует',
  CANCELLATION_MISMATCH: 'Условия отмены не соответствуют',
  CAPACITY_UNCONFIRMED: 'Вместимость не подтверждена',
};

/** Компонентные статусы, дополняющие основной статус расчета. */
export const COMPONENT_STATUS_LABEL: Record<string, string> = {
  PARTIAL_TRANSPORT_MISSING: 'Нет транспортной компоненты',
  PARTIAL_HOTEL_MISSING: 'Нет компоненты проживания',
  COMPLETE_SINGLE_SOURCE: 'Полный расчет по одному источнику',
};

export const USER_ROLE_LABEL: Record<string, string> = {
  VIEWER: 'Просмотр',
  ANALYST: 'Аналитик',
  ADMIN: 'Администратор',
};

export const DEPLOYMENT_MODE_LABEL: Record<string, string> = {
  OPEN: 'без авторизации',
  LOCAL: 'локальные учетные записи',
  OIDC: 'единый вход (OIDC)',
};

/** Среда развертывания. `prod` в интерфейсе не показывается. */
export const ENVIRONMENT_LABEL: Record<string, string> = {
  dev: 'Режим разработки',
  test: 'Тестовый режим',
  staging: 'Режим демо',
  prod: 'Промышленный режим',
};

export const EXPORT_DATASET_LABEL: Record<string, string> = {
  SCENARIO_RUNS: 'Расчеты',
  OFFERS: 'Предложения',
  SOURCE_METRICS: 'Метрики источников',
};

export const AUDIT_ACTION_LABEL: Record<string, string> = {
  LOGIN: 'Вход в систему',
  SCENARIO_CREATE: 'Создание сценария',
  SCENARIO_UPDATE: 'Изменение сценария',
  SCENARIO_ACTIVATE: 'Активация сценария',
  SCENARIO_DEACTIVATE: 'Деактивация сценария',
  SCENARIO_DELETE: 'Удаление сценария',
  SCENARIO_IMPORT: 'Импорт сценариев',
  PROFILE_CREATE: 'Создание профиля',
  PROFILE_ACTIVATE: 'Активация профиля',
  PROFILE_ARCHIVE: 'Архивация профиля',
  PROFILE_CLONE: 'Клонирование профиля',
  SOURCE_UPDATE: 'Изменение источника',
  SOURCE_ENABLE: 'Включение источника',
  SOURCE_DISABLE: 'Выключение источника',
  SOURCE_HEALTH_CHECK: 'Проверка источника',
  SOURCE_CONFIDENCE_OVERRIDE: 'Ручное значение доверия',
  MONITORING_RUN: 'Прогон мониторинга',
  SNAPSHOT_RECALCULATE: 'Пересчет снимка',
  JOB_RETRY: 'Повтор задачи',
  JOB_CANCEL: 'Отмена задачи',
  EXPORT: 'Экспорт данных',
  CACHE_PURGE: 'Очистка кэша',
  RETENTION_CLEANUP: 'Очистка по сроку хранения',
};

/** Факторы оценки качества расчета (методика §18). */
export const QUALITY_FACTOR_LABEL: Record<string, string> = {
  component_completeness: 'Полнота компонентов',
  source_count: 'Число источников',
  offer_count: 'Число предложений',
  source_agreement: 'Согласованность источников',
  freshness: 'Свежесть данных',
  connector_stability: 'Стабильность коннекторов',
};

/** Факторы доверия к источнику (методика §19). */
export const SOURCE_FACTOR_LABEL: Record<string, string> = {
  technical_stability: 'Техническая стабильность за 30 дней',
  field_completeness: 'Полнота обязательных полей',
  cross_source_agreement: 'Согласованность с другими источниками',
  valid_offer_ratio: 'Доля валидных предложений',
  schema_stability: 'Стабильность схемы',
  legal_reliability: 'Юридическая и договорная надежность',
  manual_review: 'Результаты ручной проверки',
};

/** Признаки предложения, которых может не быть в контракте источника. */
export const OFFER_ATTRIBUTE_LABEL: Record<string, string> = {
  MEAL: 'Питание',
  CANCELLATION: 'Условия отмены',
  BAGGAGE: 'Багаж',
  CAPACITY: 'Вместимость',
  RAIL_CLASS: 'Класс вагона',
};

/** Подсистемы, состояние которых показывает экран администрирования. */
export const HEALTH_COMPONENT_LABEL: Record<string, string> = {
  database: 'База данных',
  result_cache: 'Кэш результатов',
  raw_storage: 'Хранилище исходных ответов',
  profiles: 'Профили расчета',
  sources: 'Источники',
};

/** Технические метрики источника за окно наблюдения. */
export const SOURCE_METRIC_LABEL: Record<string, string> = {
  window_days: 'Окно наблюдения, дней',
  call_count: 'Обращений',
  success_rate: 'Доля успешных',
  avg_latency_ms: 'Средняя задержка, мс',
  valid_offer_ratio: 'Доля валидных предложений',
  field_completeness: 'Полнота обязательных полей',
  schema_error_rate: 'Доля ошибок схемы',
  total_offers: 'Всего предложений',
  error_breakdown: 'Ошибки по типам',
};

export const MEAL_LABEL: Record<string, string> = {
  ANY: 'Любое',
  NO_MEALS: 'Без питания',
  BREAKFAST: 'Завтрак',
  HALF_BOARD: 'Полупансион',
  FULL_BOARD: 'Полный пансион',
  ALL_INCLUSIVE: 'Все включено',
  UNKNOWN: 'Не определено',
};

export const CANCELLATION_LABEL: Record<string, string> = {
  ANY: 'Любая',
  FREE_CANCELLATION: 'Бесплатная отмена',
  NON_REFUNDABLE: 'Без возврата',
  CONDITIONAL: 'С условиями',
  UNKNOWN: 'Не определено',
};

export const FARE_TYPE_LABEL: Record<string, string> = {
  CHEAPEST: 'Самый дешевый',
  CABIN_BAGGAGE: 'С ручной кладью',
  CHECKED_BAGGAGE: 'С багажом',
};

export const RAIL_CLASS_LABEL: Record<string, string> = {
  RESERVED_SEAT: 'Плацкарт',
  COMPARTMENT: 'Купе',
};

export const TRANSPORT_LABEL: Record<string, string> = {
  AVIA: 'Авиа',
  RAIL: 'ЖД',
};

/**
 * Метки компонентных статусов уже определены выше (COMPONENT_STATUS_LABEL);
 * здесь только обертка с запасным вариантом на случай нового значения в API.
 */
export const componentStatusLabel = (status: string): string =>
  COMPONENT_STATUS_LABEL[status] ?? status;

export const REFUNDABILITY_LABEL: Record<string, string> = {
  REFUNDABLE: 'Возвратный',
  NON_REFUNDABLE: 'Невозвратный',
  UNKNOWN: 'Не указано',
};

export const BAGGAGE_LABEL: Record<string, string> = {
  CABIN_BAGGAGE: 'Ручная кладь',
  CHECKED_BAGGAGE: 'С багажом',
  UNKNOWN: 'Не указано',
};

export const SOURCE_LABEL: Record<string, string> = {
  tutu_mcp: 'Туту',
  rzd: 'РЖД',
};

export const sourceLabel = (code: string): string => SOURCE_LABEL[code] ?? code;

/** Длительность в пути: «8 ч 15 мин». */
export const duration = (minutes: number | null | undefined): string => {
  if (minutes === null || minutes === undefined) return '—';
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} мин`;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
};

/** Только время отправления или прибытия: «21:50». */
export const timeOnly = (value: string | null | undefined): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

/** Дата и время без года: «18.09, 21:50» — год виден из параметров сценария. */
export const dayTime = (value: string | null | undefined): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const day = parsed.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
  return `${day}, ${timeOnly(value)}`;
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
