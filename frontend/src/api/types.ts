/** Типы контракта /api/v1. Соответствуют docs/DATA_CONTRACT.md. */

export type UserRole = 'VIEWER' | 'ANALYST' | 'ADMIN';
export type TransportType = 'AVIA' | 'RAIL';
export type FlightFareType = 'CHEAPEST' | 'CABIN_BAGGAGE' | 'CHECKED_BAGGAGE';
export type RailClass = 'RESERVED_SEAT' | 'COMPARTMENT';
export type AccommodationType = 'HOTEL' | 'APARTMENT' | 'GUEST_HOUSE' | 'HOSTEL' | 'SANATORIUM' | 'OTHER';
export type RunStatus = 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED' | 'UNSUPPORTED' | 'NO_DATA';
export type ConfidenceLevel = 'HIGH' | 'MEDIUM' | 'LOW' | 'INSUFFICIENT';
export type SourceConfidenceLevel = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNTRUSTED';
export type SnapshotStatus = 'COLLECTING' | 'COMPLETE' | 'PARTIAL' | 'EMPTY' | 'FAILED';
export type JobStatus =
  | 'PENDING' | 'QUEUED' | 'RUNNING' | 'PARTIAL'
  | 'SUCCESS' | 'FAILED' | 'CANCELLED' | 'RETRYING' | 'TIMED_OUT';
export type ProfileStatus = 'DRAFT' | 'ACTIVE' | 'ARCHIVED';

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown>; request_id: string | null };
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface Paged<T> {
  items: T[];
  meta: PageMeta;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: UserRole;
  username: string;
  display_name: string | null;
}

export interface Principal {
  user_id: string;
  username: string;
  role: UserRole;
  display_name: string | null;
  is_anonymous: boolean;
  deployment_mode: string;
}

export interface VersionInfo {
  app_name: string;
  versions: Record<string, string>;
  deployment_mode: string;
  environment: string;
  metric_title: string;
  disclaimer: string;
  sandbox_sources_enabled: boolean;
  snapshot_interval_hours: number;
}

export interface HealthComponent {
  name: string;
  healthy: boolean;
  detail: string | null;
  metrics: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  version: Record<string, string>;
  deployment_mode: string;
  components: HealthComponent[];
  warnings: string[];
}

export interface City {
  id: string;
  code: string;
  name: string;
  region: string | null;
  supports_avia: boolean;
  supports_rail: boolean;
  is_active: boolean;
}

export interface RefOption {
  value: string;
  label: string;
  [key: string]: unknown;
}

export interface CityRef {
  id: string;
  code: string;
  name: string;
}

export interface ScenarioBrief {
  id: string;
  code: string;
  name: string;
  scenario_type: string;
  origin: CityRef | null;
  destination: CityRef | null;
  departure_date: string;
  return_date: string;
  nights: number;
  /** `null` — компонента сценарием не наблюдается. */
  transport_type: TransportType | null;
  accommodation_type: AccommodationType | null;
  stars: string;
  is_active: boolean;
}

/** Объем накопленного по сценарию — показывается перед удалением. */
export interface ScenarioFootprint {
  scenario_code?: string;
  snapshots: number;
  runs: number;
  offers: number;
  raw_responses: number;
  html_snapshots: number;
  storage_errors?: number;
}

export interface Scenario extends ScenarioBrief {
  adults: number;
  children_ages: number[];
  traveler_count: number;
  flight_fare_type: FlightFareType | null;
  rail_class: RailClass | null;
  meal_type: string;
  cancellation_filter: string;
  calculation_profile_id: string | null;
  active_from: string | null;
  active_until: string | null;
  fingerprint: string;
  priority: number;
  tags: string[];
  notes: string | null;
  created_at: string;
  created?: boolean;
}

export interface ComponentStats {
  p25: number | null;
  median: number | null;
  p75: number | null;
  min: number | null;
  max: number | null;
  source_count: number;
  offer_count: number;
  disagreement: number | null;
}

export interface RunBrief {
  id: string;
  scenario_id: string;
  scenario_code: string | null;
  scenario_name: string | null;
  market_snapshot_id: string | null;
  run_type: string;
  status: RunStatus;
  component_statuses: string[];
  observation_date: string;
  started_at: string;
  completed_at: string | null;
  lead_time_days: number;
  total_estimated_cost: number | null;
  total_p25: number | null;
  total_p75: number | null;
  /** Фактический размах допущенных предложений, не устойчивая оценка. */
  total_min: number | null;
  total_max: number | null;
  price_per_person: number | null;
  currency: string;
  quality_score: number | null;
  confidence_level: ConfidenceLevel | null;
  contains_synthetic_data: boolean;
  is_complete: boolean;
}

export interface Run extends RunBrief {
  profile: { id: string; code: string; version: string };
  versions: Record<string, string>;
  duration_ms: number | null;
  traveler_count: number;
  transport: ComponentStats;
  accommodation: ComponentStats;
  total: {
    estimated_cost: number | null;
    p25: number | null;
    p75: number | null;
    min: number | null;
    max: number | null;
    price_per_person: number | null;
    transport_share: number | null;
  };
  quality: { score: number | null; breakdown: Record<string, any> };
  confidence: { level: ConfidenceLevel | null; reason: string | null; factors: Record<string, any> };
  sources: { count: number; codes: string[] };
  offers: { valid: number; excluded: number; outliers: number };
  served_from_cache: boolean;
  error_summary: unknown[];
  scenario_detail?: Scenario | null;
  snapshot?: SnapshotBrief;
  disclaimer?: string;
}

export interface SourceBreakdownRow {
  component: string;
  source_code: string;
  eligible: boolean;
  offer_count: number;
  valid_offer_count: number;
  unclassified_offer_count: number;
  statistic: number | null;
  ineligibility_reasons: string[];
  [key: string]: unknown;
}

export interface SnapshotBrief {
  id: string;
  scenario_id: string;
  scenario_code: string | null;
  snapshot_type: string;
  status: SnapshotStatus;
  observed_at: string;
  observation_date: string;
  observation_slot: number | null;
  completed_at: string | null;
  source_codes: string[];
  transport_offer_count: number;
  accommodation_offer_count: number;
  valid_offer_count: number;
  contains_synthetic_data: boolean;
  offers_available: boolean;
}

export interface Snapshot extends SnapshotBrief {
  scenario_fingerprint: string;
  requested_at: string;
  invalid_offer_count: number;
  duplicate_offer_count: number;
  outlier_offer_count: number;
  raw_response_count: number;
  html_snapshot_count: number;
  normalization_version: string;
  collection_summary: Record<string, unknown>;
  error_summary: unknown[];
  created_by: string | null;
  created_at: string;
  is_finalized: boolean;
  runs: RunBrief[];
  run_count: number;
}

export interface SnapshotSourceRow {
  source_code: string;
  source_name: string;
  offer_type: string;
  is_synthetic: boolean;
  outcome: string;
  latency_ms: number | null;
  attempts: number;
  raw_offer_count: number;
  valid_offer_count: number;
  invalid_offer_count: number;
  unclassified_offer_count: number;
  error_code: string | null;
  error_message: string | null;
  collected_at: string;
}

export type OfferKind = 'FLIGHT' | 'RAIL' | 'ACCOMMODATION';

export interface RailOfferDetail {
  origin_station_code: string | null;
  origin_station_name: string | null;
  destination_station_code: string | null;
  destination_station_name: string | null;
  origin_city_name: string | null;
  destination_city_name: string | null;
  outbound_train_number: string | null;
  inbound_train_number: string | null;
  outbound_departure_at: string | null;
  outbound_arrival_at: string | null;
  inbound_departure_at: string | null;
  inbound_arrival_at: string | null;
  outbound_duration_minutes: number | null;
  inbound_duration_minutes: number | null;
  carriers: string[];
  car_type: string | null;
  car_type_raw: string | null;
  service_classes: string[];
  available_places_outbound: number | null;
  available_places_inbound: number | null;
  is_two_storey: boolean | null;
  price_per_place_outbound: number | null;
  price_per_place_inbound: number | null;
  passenger_count: number;
  is_round_trip: boolean;
  refundability: string;
}

export interface FlightOfferDetail {
  origin_code: string | null;
  origin_name: string | null;
  destination_code: string | null;
  destination_name: string | null;
  outbound_departure_at: string | null;
  outbound_arrival_at: string | null;
  inbound_departure_at: string | null;
  inbound_arrival_at: string | null;
  outbound_stops: number | null;
  inbound_stops: number | null;
  outbound_duration_minutes: number | null;
  inbound_duration_minutes: number | null;
  marketing_carriers: string[];
  flight_numbers: string[];
  cabin_class: string | null;
  fare_family: string | null;
  baggage_type: string;
  baggage_raw: string | null;
  refundability: string;
  is_round_trip: boolean;
  passenger_count: number;
}

export interface AccommodationOfferDetail {
  property_name: string | null;
  property_key: string | null;
  accommodation_type: string;
  accommodation_type_raw: string | null;
  stars: number | null;
  stars_status: string;
  address: string | null;
  city_name: string | null;
  check_in: string | null;
  check_out: string | null;
  nights: number | null;
  room_name: string | null;
  room_count: number;
  max_guests: number | null;
  capacity_confirmed: boolean;
  adults: number | null;
  children_ages: number[];
  meal_type: string;
  meal_raw: string | null;
  cancellation_type: string;
  review_score: number | null;
  review_count: number | null;
}

/** Какой из трех наборов пришел, определяется полем `offer_type`. */
export type OfferDetail = RailOfferDetail | FlightOfferDetail | AccommodationOfferDetail;

export interface Offer {
  id: string;
  source_code: string;
  offer_type: string;
  currency: string;
  total_price: number | null;
  price_per_night: number | null;
  validity_status: string;
  classification_status: string;
  exclusion_reason: string;
  exclusion_detail: string | null;
  equivalence_group_id?: string | null;
  is_duplicate: boolean;
  is_outlier: boolean;
  matches_profile: boolean;
  deeplink?: string | null;
  detail?: OfferDetail | null;
}

/** Ответ, когда предложений нет по объяснимой причине (снимок очищен). */
export interface OffersUnavailable {
  offers_available: false;
  reason: 'NO_SNAPSHOT' | 'PURGED';
  offers_purged_at?: string | null;
  items: [];
  groups: [];
}

export type RunOffersResponse =
  | (Paged<Offer> & { offers_available: true; run_id: string; snapshot_id: string })
  | (OffersUnavailable & { run_id: string; meta: Paged<Offer>['meta'] });

export interface RailPriceEntry {
  offer_id: string;
  total_price: number | null;
  price_per_place_outbound: number | null;
  price_per_place_inbound: number | null;
  available_places_outbound: number | null;
  available_places_inbound: number | null;
  exclusion_reason: string;
  is_outlier: boolean;
  deeplink: string | null;
}

export interface RailComparisonGroup {
  equivalence_group_id: string | null;
  outbound_train_number: string | null;
  inbound_train_number: string | null;
  outbound_departure_at: string | null;
  outbound_arrival_at: string | null;
  inbound_departure_at: string | null;
  inbound_arrival_at: string | null;
  outbound_duration_minutes: number | null;
  inbound_duration_minutes: number | null;
  car_type: string | null;
  car_type_raw: string | null;
  origin_station_name: string | null;
  destination_station_name: string | null;
  carriers: string[];
  sources_disagree_on_schedule: boolean;
  car_type_ambiguous: boolean;
  prices: Record<string, RailPriceEntry>;
  min_price: number | null;
  max_price: number | null;
  delta_absolute: number | null;
  delta_ratio: number | null;
  cheapest_source: string | null;
  source_count: number;
}

export interface RailComparisonSummary {
  group_count: number;
  cross_source_group_count: number;
  single_source_group_count: number;
  median_delta_ratio: number | null;
  max_delta_ratio: number | null;
}

export interface RailComparison {
  run_id: string;
  snapshot_id?: string;
  offers_available: boolean;
  reason?: 'NO_SNAPSHOT' | 'PURGED';
  sources: string[];
  groups: RailComparisonGroup[];
  truncated: boolean;
  summary: RailComparisonSummary;
}

export interface Source {
  id: string;
  code: string;
  name: string;
  category: string;
  protocol: string;
  offer_types: string[];
  qualification_status: string;
  is_enabled: boolean;
  is_synthetic: boolean;
  is_usable: boolean;
  /** Признаки, которых нет в контракте источника (см. OfferAttribute). */
  unreported_attributes?: string[];
  legal_status: string | null;
  storage_allowed: boolean;
  html_storage_allowed: boolean;
  horizon: {
    min_supported_date: string | null;
    max_supported_date: string | null;
    booking_horizon_days: number | null;
    checked_at: string | null;
  };
  circuit: { consecutive_failures: number; open_until: string | null; is_open: boolean };
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  confidence?: SourceConfidence | null;
}

export interface SourceConfidence {
  id: string;
  source_code: string | null;
  calculation_date: string;
  formula_version: string;
  score: number;
  effective_score: number;
  level: SourceConfidenceLevel;
  input_metrics: Record<string, unknown>;
  factor_scores: Record<string, number>;
  manual_override: number | null;
  override_reason: string | null;
  approved_by: string | null;
}

export interface SourceOverviewRow {
  code: string;
  name: string;
  category: string;
  offer_types: string[];
  is_enabled: boolean;
  qualification_status: string;
  last_run_at?: string | null;
  confidence_score?: number | null;
  confidence_level?: string | null;
  [key: string]: unknown;
}

export interface Profile {
  id: string;
  code: string;
  name: string;
  version: string;
  version_seq: number;
  status: ProfileStatus;
  label: string;
  activated_at: string | null;
  archived_at: string | null;
  description?: string | null;
  rules?: Record<string, any>;
  has_runs?: boolean;
}

export interface Job {
  job_id: string;
  job_type: string;
  status: JobStatus;
  scenario_id: string | null;
  market_snapshot_id: string | null;
  scenario_run_id: string | null;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  progress: { current: number; total: number; ratio: number | null; message: string | null };
  attempts: number;
  max_attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  created_by: string | null;
  status_url: string;
  run?: Run;
  snapshot?: SnapshotBrief;
  cached?: boolean;
}

export interface JobEvent {
  status: string;
  message: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface DashboardOverview {
  scenario_count?: number;
  median_total_cost?: number | null;
  [key: string]: unknown;
}

/** Строка сравнения направлений (GET /dashboard/directions). */
export interface DirectionRow {
  origin_city_code?: string;
  origin_city_name?: string;
  destination_city_code?: string;
  destination_city_name?: string;
  transport_type?: string;
  scenario_count?: number;
  complete_count?: number;
  median_total_cost?: number | null;
  median_transport?: number | null;
  median_hotel?: number | null;
  p25?: number | null;
  p75?: number | null;
  min?: number | null;
  max?: number | null;
  change_1d?: number | null;
  change_7d?: number | null;
  change_30d?: number | null;
  avg_quality_score?: number | null;
  confidence_levels?: Record<string, number>;
  last_update?: string | null;
  contains_synthetic?: boolean;
  // Эндпоинт /dashboard/changes возвращает строки другой формы:
  // направление приходит как origin/destination (названия городов), а не
  // как origin_city_name/destination_city_name.
  scenario_id?: string;
  scenario_code?: string;
  scenario_name?: string;
  origin?: string;
  destination?: string;
  previous_total?: number | null;
  current_total?: number | null;
  change?: number | null;
  direction?: 'UP' | 'DOWN';
  observation_date?: string;
}

export interface DeparturePoint {
  departure_date: string;
  lead_time_days: number;
  /** Отношение к типичной стоимости своего направления. */
  price_index: number;
  median_total_cost: number | null;
  scenario_count: number;
  routes: number;
}

export interface DepartureDates {
  points: DeparturePoint[];
  comparable_routes: number;
  cheapest: DeparturePoint | null;
  dearest: DeparturePoint | null;
  note: string;
}

export interface SourceGapRow {
  origin_city_code: string;
  origin_city_name: string;
  destination_city_code: string;
  destination_city_name: string;
  transport_type: string;
  matched_trains: number;
  median_gap: number;
  max_gap: number;
}

export interface SourceGap {
  items: SourceGapRow[];
  summary: { matched_trains: number; median_gap: number | null; max_gap: number | null };
  note?: string;
}

export interface Premium {
  from: string;
  to: string;
  premium: number | null;
  base: number | null;
  upgraded: number | null;
  snapshots: number;
}

export interface PriceComposition {
  stars: Premium;
  baggage: Premium;
  direct: Premium;
  note?: string;
}

export interface SpreadRow {
  origin_city_code: string;
  origin_city_name: string;
  destination_city_code: string;
  destination_city_name: string;
  transport_type: string;
  median_total_cost: number | null;
  min: number | null;
  max: number | null;
  p25: number | null;
  p75: number | null;
  spread_ratio: number | null;
  iqr_ratio: number | null;
  review_score: number | null;
  review_sample: number;
}

export interface Spread {
  items: SpreadRow[];
  summary: {
    median_spread_ratio: number | null;
    median_iqr_ratio: number | null;
    widest: SpreadRow | null;
  };
  note?: string;
}

export interface Template {
  id: string;
  code: string;
  name: string;
  description: string | null;
  defaults: Record<string, unknown>;
  is_active: boolean;
}

export interface AuditEvent {
  id: string;
  action: string;
  actor: { user_id: string | null; username: string | null; role: string | null };
  object_type: string | null;
  object_id: string | null;
  summary: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

// --- Витрина вариантов отдыха ---

export interface ShowcaseCities {
  items: { code: string; name: string }[];
  horizon_days: number;
}

/**
 * Цена вместе с тем, на чем она стоит.
 *
 * Медиана идет в паре с минимумом: человек едет туда-обратно одним поездом и
 * берет из дешевой половины выдачи, поэтому одна медиана систематически выше
 * того, что он платит. Рядом всегда число предложений и источников — медиана
 * по четырем и по сорока выглядят одинаково, а доверия заслуживают разного.
 */
export interface ShowcasePrice {
  median: number;
  /** Самое дешевое допущенное предложение: уже после фильтров и выбросов. */
  min: number | null;
  offers: number;
  sources: number;
  /** Расчет, из которого взята цифра: без него от нее некуда перейти. */
  run_id: string | null;
  /** Цена единицы наблюдения — ночи у проживания. */
  per_unit?: number;
  /** Цифра получена пересчетом «ночь × число ночей», а не наблюдением брони. */
  estimated?: boolean;
  observed_nights?: number;
}

/** Плечо поездки: половина маршрута, наблюдавшаяся отдельным сценарием. */
export interface ShowcaseLeg {
  direction: 'OUTBOUND' | 'INBOUND';
  date: string;
  median: number;
  min: number | null;
  run_id: string;
}

export interface ShowcaseOption {
  destination_code: string;
  destination_name: string;
  /** Та цена проезда, которая на эти даты существует (см. `transport_basis`). */
  transport: ShowcasePrice | null;
  /** Круговой тариф: цена реальной покупки, но только на наблюдаемую длительность. */
  transport_round_trip: ShowcasePrice | null;
  /** Два билета: складывается на любой интервал, но в сумме дороже кругового. */
  transport_two_legs: (ShowcasePrice & { legs: ShowcaseLeg[] }) | null;
  transport_basis: 'ROUND_TRIP' | 'TWO_LEGS' | null;
  accommodation: ShowcasePrice | null;
  /** У итога `min` — сумма минимумов, то есть нижняя граница, а не предложение. */
  total: (ShowcasePrice & { estimated?: boolean }) | null;
  /** Каких компонент не хватило: пустая строка иначе читается как бесплатная поездка. */
  missing: string[];
  /** Число получено разовым расчетом на дату вне сетки, а не наблюдением. */
  on_demand: boolean;
}

export interface ShowcaseOptions {
  origin_code: string;
  origin_name: string;
  /** Методика витрины: разовый расчет для дат вне сетки идет по ней же. */
  profile_id: string | null;
  departure_date: string;
  return_date: string;
  observation_date: string | null;
  nights: number;
  transport_type: string;
  stars: string;
  travelers: number;
  items: ShowcaseOption[];
  available: boolean;
  grid_nights: number;
  stay_nights_observed: number;
  disclaimer: string;
}

export interface ShowcaseCurvePoint {
  price: number;
  min: number | null;
  offers: number;
  sources: number;
  run_id: string;
}

export interface ShowcaseTransportCurve {
  origin_code: string;
  origin_name: string;
  transport_type: string;
  direction: string;
  travelers: number;
  horizon_days: number;
  observation_date: string | null;
  series: {
    destination_code: string;
    destination_name: string;
    points: (ShowcaseCurvePoint & { departure_date: string })[];
  }[];
  disclaimer: string;
}

export interface ShowcaseAccommodationCurve {
  stars: string;
  nights: number;
  travelers: number;
  horizon_days: number;
  observation_date: string | null;
  series: {
    city_code: string;
    city_name: string;
    points: (ShowcaseCurvePoint & { check_in: string })[];
  }[];
  disclaimer: string;
}

export interface ShowcaseObservationDates {
  items: { observation_date: string; runs: number }[];
  latest: string | null;
}

/** Клетка матрицы покрытия: состояние наблюдения и размер выборки за ним. */
export interface CoverageCell {
  date: string;
  state: 'OK' | 'THIN' | 'PARTIAL' | 'NO_DATA' | 'NOT_OBSERVED';
  run_id: string | null;
  price: number | null;
  offers: number;
  sources: number;
}

export interface CoverageMatrix {
  observation_date: string | null;
  dates: string[];
  thin_threshold: number;
  transport: {
    origin_code: string;
    origin_name: string;
    destination_code: string;
    destination_name: string;
    transport_type: string;
    cells: CoverageCell[];
  }[];
  accommodation: {
    city_code: string;
    city_name: string;
    stars: string;
    cells: CoverageCell[];
  }[];
  legend: Record<string, string>;
}

export interface ShowcaseQuality {
  date: string;
  planned: number;
  collected: number;
  missing: number;
  missing_ratio: number;
  duration_minutes: number | null;
  snapshot_statuses: Record<string, number>;
  run_statuses: Record<string, number>;
  partial_source_results: number;
  avg_transport_offers: number | null;
  avg_accommodation_offers: number | null;
  source_outcomes: {
    source_code: string;
    offer_type: string;
    outcome: string;
    count: number;
    meaning: string;
  }[];
  failed: boolean;
  stay_estimate_accuracy: {
    pairs: number;
    median_deviation: number | null;
    rows: {
      city_code: string;
      stars: string;
      check_in: string;
      nights: number;
      estimate: number;
      observed_booking: number;
      deviation: number | null;
    }[];
  };
}

/** Шаг денежной воронки: во сколько обошлось правило отбора. */
export interface FunnelStep {
  step: string;
  title: string;
  removed: number;
  reasons: Record<string, number>;
  count: number;
  median: number | null;
  min: number | null;
  max: number | null;
  p25: number | null;
  p75: number | null;
  median_delta?: number | null;
}

export interface RunFunnel {
  run_id: string;
  snapshot_id?: string;
  available: boolean;
  reason?: string;
  component?: string;
  steps?: FunnelStep[];
}

export interface RunWhatIf {
  run_id: string;
  available: boolean;
  reason?: string;
  component?: string;
  official?: FunnelStep;
  revised?: FunnelStep;
  median_delta?: number | null;
  preliminary?: boolean;
  note?: string;
  switches?: { kind: string; code: string; title: string; offers: number }[];
}
