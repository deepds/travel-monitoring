/**
 * HTTP-клиент к /api/v1.
 *
 * Токен хранится в localStorage: интерфейс работает за nginx, который
 * проксирует /api на backend, поэтому CORS и cookie не задействованы.
 */

import type {
  ApiErrorBody, AuditEvent, City, DashboardOverview, DirectionRow, HealthResponse,
  Job, JobEvent, Offer, Paged, Principal, Profile, RailComparison, RefOption, Run,
  RunBrief, RunOffersResponse, Scenario, ScenarioBrief, ScenarioFootprint, Snapshot,
  SnapshotBrief, SnapshotSourceRow, Source, SourceBreakdownRow, SourceConfidence,
  SourceOverviewRow, Template, TokenResponse, VersionInfo,
} from './types';

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';
const TOKEN_KEY = 'tco.token';

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly details: Record<string, unknown> = {},
    public readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

/** Вызывается при 401, чтобы приложение вернуло пользователя на вход. */
let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (handler: () => void) => {
  onUnauthorized = handler;
};

type Query = Record<string, string | number | boolean | string[] | null | undefined>;

function buildQuery(params?: Query): string {
  if (!params) return '';
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)));
    } else {
      search.append(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

async function request<T>(
  method: string,
  path: string,
  options: { query?: Query; body?: unknown; raw?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${BASE}${path}${buildQuery(options.query)}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (response.status === 401 && onUnauthorized) {
    tokenStore.clear();
    onUnauthorized();
  }

  if (!response.ok) {
    let code = 'HTTP_ERROR';
    let message = `Ошибка ${response.status}`;
    let details: Record<string, unknown> = {};
    let requestId: string | null = response.headers.get('X-Request-Id');
    try {
      const body = (await response.json()) as ApiErrorBody;
      if (body?.error) {
        code = body.error.code;
        message = body.error.message;
        details = body.error.details ?? {};
        requestId = body.error.request_id ?? requestId;
      }
    } catch {
      // Тело может быть пустым или не-JSON — сохраняем сообщение по статусу.
    }
    throw new ApiError(code, message, response.status, details, requestId);
  }

  if (options.raw) return response as unknown as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T>(path: string, query?: Query) => request<T>('GET', path, { query });
const post = <T>(path: string, body?: unknown, query?: Query) =>
  request<T>('POST', path, { body, query });
const patch = <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body });
const del = <T>(path: string) => request<T>('DELETE', path);

export const api = {
  // --- Аутентификация ---
  login: (username: string, password: string) =>
    post<TokenResponse>('/auth/login', { username, password }),
  me: () => get<Principal>('/auth/me'),

  // --- Служебное ---
  health: () => get<HealthResponse>('/health'),
  version: () => get<VersionInfo>('/version'),

  // --- Справочники ---
  cities: () => get<{ items: City[]; total: number }>('/reference/cities'),
  refOptions: (kind: string) => get<{ items: RefOption[]; note?: string }>(`/reference/${kind}`),
  horizon: () => get<Record<string, any>>('/reference/horizon'),
  templates: () => get<{ items: Template[]; total: number }>('/templates'),
  instantiateTemplate: (id: string, body: Record<string, unknown>) =>
    post<Scenario>(`/templates/${id}/instantiate`, body),

  // --- Сценарии ---
  scenarios: (query?: Query) => get<Paged<ScenarioBrief>>('/scenarios', query),
  scenario: (id: string) => get<Scenario>(`/scenarios/${id}`),
  createScenario: (body: Record<string, unknown>) => post<Scenario>('/scenarios', body),
  patchScenario: (id: string, body: Record<string, unknown>) =>
    patch<Scenario>(`/scenarios/${id}`, body),
  activateScenario: (id: string) => post<Scenario>(`/scenarios/${id}/activate`),
  deactivateScenario: (id: string) => post<Scenario>(`/scenarios/${id}/deactivate`),
  scenarioFootprint: (id: string) => get<ScenarioFootprint>(`/scenarios/${id}/footprint`),
  /** `purgeData` уничтожает накопленные наблюдения безвозвратно. */
  deleteScenario: (id: string, purgeData = false) =>
    del<{ success: boolean; purged: boolean; message: string; removed?: ScenarioFootprint }>(
      `/scenarios/${id}?purge_data=${purgeData}`,
    ),

  // --- Расчеты ---
  createCalculation: (body: { scenario_id?: string; scenario?: unknown; profile_id?: string; force_refresh?: boolean }) =>
    post<Job & { cached: boolean; run?: Run }>('/calculations', body),
  calculation: (jobId: string) => get<Job>(`/calculations/${jobId}`),
  cancelCalculation: (jobId: string) => post<Job>(`/calculations/${jobId}/cancel`),

  // --- Результаты ---
  runs: (query?: Query) => get<Paged<RunBrief>>('/scenario-runs', query),
  run: (id: string) => get<Run>(`/scenario-runs/${id}`),
  runExplain: (id: string) => get<Record<string, any>>(`/scenario-runs/${id}/explain`),
  runSourceBreakdown: (id: string) =>
    get<{ rows?: SourceBreakdownRow[]; [key: string]: unknown }>(`/scenario-runs/${id}/source-breakdown`),
  runOffers: (id: string, query?: Query) =>
    get<RunOffersResponse>(`/scenario-runs/${id}/offers`, query),
  runRailComparison: (id: string, query?: Query) =>
    get<RailComparison>(`/scenario-runs/${id}/rail-comparison`, query),

  // --- Снимки рынка ---
  snapshots: (query?: Query) => get<Paged<SnapshotBrief>>('/market-snapshots', query),
  snapshot: (id: string) => get<Snapshot>(`/market-snapshots/${id}`),
  snapshotOffers: (id: string, query?: Query) =>
    get<Paged<Offer> & { snapshot_id: string }>(`/market-snapshots/${id}/offers`, query),
  snapshotSources: (id: string) =>
    get<{ items: SnapshotSourceRow[]; [key: string]: unknown }>(`/market-snapshots/${id}/sources`),
  snapshotArtifacts: (id: string) => get<Record<string, any>>(`/market-snapshots/${id}/raw-artifacts`),
  recalculateSnapshot: (id: string, body: { profile_id?: string; profile_code?: string }) =>
    post<Record<string, any>>(`/market-snapshots/${id}/recalculate`, body),

  // --- Дашборд ---
  overview: (query?: Query) => get<DashboardOverview>('/dashboard/overview', query),
  directions: (query?: Query) => get<{ items: DirectionRow[]; total: number }>('/dashboard/directions', query),
  trends: (query?: Query) => get<Record<string, any>>('/dashboard/trends', query),
  costStructure: (query?: Query) => get<Record<string, any>>('/dashboard/cost-structure', query),
  changes: (query?: Query) => get<{ items: DirectionRow[]; total: number }>('/dashboard/changes', query),
  quality: (query?: Query) => get<Record<string, any>>('/dashboard/quality', query),
  kpi: (query?: Query) => get<Record<string, any>>('/dashboard/kpi', query),
  scenarioHistory: (id: string, query?: Query) =>
    get<{ items: Record<string, any>[]; total: number; scenario: Record<string, string> }>(
      `/dashboard/scenarios/${id}/history`, query),

  // --- Источники ---
  sources: (query?: Query) => get<{ items: Source[]; total: number }>('/sources', query),
  sourcesOverview: (query?: Query) =>
    get<{ items: SourceOverviewRow[]; total: number; window_days: number }>('/sources/overview', query),
  source: (id: string) => get<Source>(`/sources/${id}`),
  patchSource: (id: string, body: Record<string, unknown>) => patch<Source>(`/sources/${id}`, body),
  enableSource: (id: string) => post<Source>(`/sources/${id}/enable`),
  disableSource: (id: string) => post<Source>(`/sources/${id}/disable`),
  healthCheckSource: (id: string) => post<Record<string, any>>(`/sources/${id}/health-check`),
  sourceMetrics: (id: string, query?: Query) => get<Record<string, any>>(`/sources/${id}/metrics`, query),
  sourceConfidence: (id: string) => get<Record<string, any>>(`/sources/${id}/confidence`),
  recalcConfidence: (id: string) => post<SourceConfidence>(`/sources/${id}/confidence/recalculate`),
  overrideConfidence: (id: string, body: { score: number; reason: string }) =>
    post<SourceConfidence>(`/sources/${id}/confidence/override`, body),

  // --- Профили ---
  profiles: (query?: Query) => get<{ items: Profile[]; total: number }>('/calculation-profiles', query),
  profile: (id: string) => get<Profile>(`/calculation-profiles/${id}`),
  activateProfile: (id: string) => post<Profile>(`/calculation-profiles/${id}/activate`),
  archiveProfile: (id: string) => post<Profile>(`/calculation-profiles/${id}/archive`),
  cloneProfile: (id: string, body: Record<string, unknown>) =>
    post<Profile>(`/calculation-profiles/${id}/clone`, body),
  patchProfile: (id: string, body: Record<string, unknown>) =>
    patch<Profile>(`/calculation-profiles/${id}`, body),

  // --- Задачи ---
  jobs: (query?: Query) => get<Paged<Job>>('/jobs', query),
  job: (id: string) => get<Job>(`/jobs/${id}`),
  jobEvents: (id: string) => get<{ items: JobEvent[]; total: number }>(`/jobs/${id}/events`),
  retryJob: (id: string) => post<Job>(`/jobs/${id}/retry`),
  cancelJob: (id: string) => post<Job>(`/jobs/${id}/cancel`),

  // --- Мониторинг ---
  monitoringStatus: () => get<Record<string, any>>('/admin/monitoring/status'),
  monitoringJobs: (query?: Query) => get<Paged<Job>>('/admin/monitoring/jobs', query),
  runMonitoring: (body: { force_refresh?: boolean; limit?: number }) =>
    post<Record<string, any>>('/admin/monitoring/run', body),
  runMonitoringScenario: (id: string, body: { force_refresh?: boolean }) =>
    post<Record<string, any>>(`/admin/monitoring/scenarios/${id}/run`, body),

  // --- Экспорт ---
  createExport: (body: Record<string, unknown>, query?: Query) =>
    post<Record<string, any>>('/exports', body, query),
  exportJob: (id: string) => get<Job & { artifact?: Record<string, any> }>(`/exports/${id}`),
  downloadExportUrl: (id: string) => `${BASE}/exports/${id}/download`,

  // --- Импорт каталога ---
  importScenarios: async (file: File, activate: boolean) => {
    const form = new FormData();
    form.append('file', file);
    const token = tokenStore.get();
    const fmt = /\.(ya?ml)$/i.test(file.name) ? 'yaml' : 'csv';
    const response = await fetch(
      `${BASE}/admin/scenarios/import?format=${fmt}&activate=${activate}`,
      { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form },
    );
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
      throw new ApiError(
        body?.error?.code ?? 'IMPORT_ERROR',
        body?.error?.message ?? `Ошибка ${response.status}`,
        response.status,
      );
    }
    return (await response.json()) as Record<string, any>;
  },

  // --- Аудит ---
  auditEvents: (query?: Query) => get<Paged<AuditEvent>>('/audit/events', query),
};

/**
 * Скачивает защищенный файл: обычная ссылка не несет заголовок авторизации,
 * поэтому файл забирается fetch-ом и сохраняется через временный object URL.
 */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const token = tokenStore.get();
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(
      body?.error?.code ?? 'DOWNLOAD_ERROR',
      body?.error?.message ?? `Не удалось скачать файл (${response.status})`,
      response.status,
    );
  }

  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = match?.[1] ?? fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
