/**
 * Admin data client (P2 Admin, Task 8.1) - REAL wiring to `/api/v1/admin/*`.
 *
 * Replaces the former UI-only mock. Access is enforced SERVER-SIDE by the admin
 * capability on every endpoint (hiding UI is never the boundary). Requests go
 * through {@link apiFetch} with `credentials: 'include'`; mutations carry the
 * double-submit CSRF token injected by the client. The backend error envelope
 * `{ error: { code, message } }` is surfaced as {@link AdminApiError} so the UI
 * can branch on machine codes (`last_active_admin`, `confirm_mismatch`, ...).
 *
 * Every shape here mirrors a backend Pydantic response model exactly (camelCase,
 * allowlisted). No secrets/content ever cross this boundary.
 */
import { apiDelete, apiFetch, apiPatch, apiPost } from './client';
import type { ErrorReportPayload } from './error-reports';

export type AdminUserRole = 'user' | 'admin';

/** An admin invite lifecycle record. Never carries the raw invite value;
 *  `id` is the safe handle for revoke. Nullable lifecycle fields are always
 *  present so clients can distinguish absence from an older response shape. */
export interface AdminInviteView {
  id: string;
  email: string;
  role: string;
  createdBy: string | null;
  createdAt: string;
  expiresAt: string;
  status: 'active' | 'used' | 'expired' | 'revoked' | 'superseded';
  usedAt: string | null;
  usedBy: string | null;
  revokedAt: string | null;
  revokedBy: string | null;
  revokeReason: string | null;
}

export interface AdminInviteList {
  items: AdminInviteView[];
}

/** Returned ONCE at creation - `inviteUrl` embeds the single-use token. */
export interface CreatedInvite {
  id: string;
  email: string;
  role: string;
  expiresAt: string;
  inviteUrl: string;
}
export type AdminUserStatus = 'active' | 'disabled' | 'pending_verification';
export type MetricName = 'signups' | 'active_users' | 'resumes_tailored';
export type MetricWindow = 7 | 30 | 90;

export interface AdminStats {
  totalUsers: number;
  activeUsers: number;
  disabledUsers: number;
  totalResumes: number;
  resumesTailored: number;
  applications: number;
  coverLettersGenerated: number;
  interviewPrepsGenerated: number;
  outreachGenerated: number;
  signups: number;
  computedAt: string;
  stale: boolean;
}

export interface AdminUserRow {
  id: string;
  name: string;
  email: string;
  avatarUrl: string | null;
  role: AdminUserRole;
  status: AdminUserStatus;
  emailVerified: boolean;
  createdAt: string;
  deletedAt?: string | null;
  purgeDueAt?: string | null;
  resumeCount: number;
  applicationCount: number;
  lastActiveAt?: string | null;
}

export interface AdminUserList {
  items: AdminUserRow[];
  nextCursor: string | null;
}

export interface AuditEntry {
  id: string;
  ts: string;
  event: string;
  actorUserId?: string | null;
  targetUserId?: string | null;
  ipHash?: string | null;
  requestId?: string | null;
  meta?: Record<string, unknown> | null;
}

export interface AuditList {
  items: AuditEntry[];
  nextCursor: string | null;
}

export interface AdminUserDetail extends AdminUserRow {
  updatedAt: string;
  tailoredCount: number;
  /** `password`, `oauth:<providers>`, `password+oauth:<providers>`, or `unknown`. */
  signupMethod: string;
  /** Whether saved AI credentials exist; this does not verify they work. */
  aiConfigured: boolean;
  recentAudit: AuditEntry[];
}

export interface UsageSeriesPoint {
  date: string; // YYYY-MM-DD (UTC day)
  value: number;
}

export interface UsageSeries {
  metric: string;
  window: number;
  points: UsageSeriesPoint[];
  computedAt: string;
}

export interface MutationResult {
  changed: boolean;
  user?: AdminUserRow | null;
}

// ---------------------------------------------------------------------------
// Observability: System Health (Req 3, 8, 17) - mirrors backend `AdminHealth`
// ---------------------------------------------------------------------------

/** Traffic-light state of one subsystem tile. */
export type HealthStatus = 'ok' | 'degraded' | 'down';

/** One subsystem tile (Backend / Database / KVStore-Queue / AI / Storage / Migrations). */
export interface HealthTile {
  name: string;
  status: HealthStatus;
  detail?: string | null;
}

/** Secret-free release / deployment metadata (Req 17). */
export interface ReleaseInfo {
  version: string;
  build?: string | null;
  commit?: string | null;
  migrationApplied?: string | null;
  migrationHead?: string | null;
  env: string;
}

/** One background-job row, shared by `AdminHealth` and the Jobs panel (Req 8). */
export interface JobRow {
  name: string;
  lastRun?: string | null;
  lastOutcome?: 'success' | 'failure' | 'skipped' | null;
  lagSeconds?: number | null;
  nextRun?: string | null;
  lastSuccess?: string | null;
  runningSince?: string | null;
  currentDurationSeconds?: number | null;
  expectedDurationSeconds?: number | null;
  potentiallyStuck?: boolean;
  lockState?: 'held' | 'free' | null;
}

/** `GET /admin/health` - six subsystem tiles + release fields + jobs table. */
export interface AdminHealth {
  tiles: HealthTile[];
  release: ReleaseInfo;
  backendUptimeSeconds?: number | null;
  jobs: JobRow[];
  computedAt: string;
  stale: boolean;
}

/** `GET /admin/jobs` - per-job status table + worker-independent gauges (Req 8).
 *
 * The queue, dead-letter, and purge gauges are optional and each carries an
 * explicit `*Unavailable` flag so the UI can distinguish zero from a failed
 * read and render an unavailable indicator rather than a bogus 0. */
export interface JobsPanel {
  jobs: JobRow[];
  queueLength?: number | null;
  queueLengthUnavailable: boolean;
  purgeBacklog?: number | null;
  purgeBacklogUnavailable: boolean;
  deadLetterCount?: number | null;
  deadLetterCountUnavailable: boolean;
  computedAt: string;
  stale: boolean;
}

export interface BulkDisableResult {
  results: { id: string; result: string }[];
  disabled: number;
  skipped: number;
}

// ---------------------------------------------------------------------------
// Observability: AI analytics (Req 4) - mirrors backend `AiAnalytics`.
//
// Allowlisted, secret-free call aggregates + a per-provider breakdown + a
// nullable decimal-dollar estimate. `daily` is the AI-calls series for an
// optional chart / data table; `dataScope` defines what observations contributed.
// ---------------------------------------------------------------------------

/** One point in a daily aggregate series (UTC day -> integer value). */
export interface SeriesPoint {
  date: string; // YYYY-MM-DD (UTC day)
  value: number;
}

/** Per-provider AI call count (closed provider enumeration - Req 20.3). */
export interface ProviderCount {
  provider: string;
  calls: number;
}

/** `GET /admin/ai-analytics?window=` - allowlisted AI aggregates + cost estimate. */
export interface AiAnalytics {
  window: number;
  totalCalls: number;
  /** 4dp fraction; `successRate + failureRate === 1.0` when `totalCalls > 0`. */
  successRate: number;
  /** 4dp fraction; both rates are `0.0` when `totalCalls === 0`. */
  failureRate: number;
  avgLatencyMs: number;
  /** Average AI tokens per call (named `avgUnitsPerCall` to match the backend). */
  avgUnitsPerCall: number;
  timeouts: number;
  retries: number;
  /** Decimal-dollar estimate; `null` when pricing cannot cover the data. */
  estimatedCostDollars: number | null;
  costUnavailable: boolean;
  costUnavailableReason?: string | null;
  dataScope: string;
  providers: ProviderCount[];
  daily?: SeriesPoint[] | null;
  computedAt: string;
}

// ---------------------------------------------------------------------------
// Observability: Errors summary (Req 5) - mirrors backend `ErrorsSummary`.
//
// Grouped buckets ONLY: aggregate 4xx/5xx counts, a top-route-class failure
// list (may be empty), by-source failure counts, and a daily error-count
// trend. Never a raw log line, stack trace, exception, replay or trace - the
// dashboard is an operational summary, not a log/trace explorer (Req 21.2).
// ---------------------------------------------------------------------------

/** One grouped route-class failure count (route *class*, not a raw path). */
export interface RouteClassFailures {
  routeClass: string;
  failures: number;
}

/** Failure counts grouped by originating subsystem (absent sources report 0). */
export interface ErrorsBySource {
  api: number;
  job: number;
  storage: number;
  ai: number;
}

/** `GET /admin/errors?window=` - grouped UTC-date-bucket errors and trend. */
export interface ErrorsSummary {
  window: number;
  /** Inclusive UTC calendar-date boundaries (`YYYY-MM-DD`). */
  windowStartDate: string;
  windowEndDate: string;
  granularity: 'utc_day';
  dataScope: string;
  counts4xx: number;
  counts5xx: number;
  topRouteClasses: RouteClassFailures[];
  bySource: ErrorsBySource;
  trend: SeriesPoint[];
  /** Field paths without instrumentation (`bySource.job` and
   *  `bySource.storage`) - render "Not instrumented" instead of a misleading
   *  zero. Route classes are current-process observations and are not listed. */
  notInstrumented: string[];
  computedAt: string;
}

/** One user-submitted, privacy-minimized Tailor failure report. */
export interface AdminErrorReport extends ErrorReportPayload {
  id: string;
  createdAt: string;
  user: {
    id: string;
    name: string;
    email: string;
  };
}

export interface AdminErrorReportList {
  items: AdminErrorReport[];
  nextCursor: string | null;
}

export interface ErrorReportListParams {
  cursor?: string | null;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Observability: Performance signals (Req 6) - mirrors backend `PerformanceSignals`.
//
// Latency/cache aggregates come from bounded current-process instrumentation.
// Host metrics are omitted server-side via `exclude_none`, so they arrive as
// `undefined` and are simply not rendered. `dbQueryTimeMs` is likewise omitted
// when there is no source; its field name then appears in `unavailable` so the
// client can show an explicit "unavailable" indicator (Req 6.7) rather than a
// bogus value. `p95Ms` is optional per route-class (only present where the
// stored aggregate supports it - Req 6.2).
// ---------------------------------------------------------------------------

/** Average (and optional p95) latency for one route class (Req 6.1/6.2). */
export interface RouteClassLatency {
  routeClass: string;
  avgMs: number;
  /** Present only where the stored aggregate supports a percentile (Req 6.2). */
  p95Ms?: number | null;
}

/** Average duration for one slow background job (Req 6.3). */
export interface SlowJob {
  name: string;
  avgMs: number;
}

/** `GET /admin/performance` - latency/cache aggregates + slow routes/jobs (Req 6).
 *
 * Host metrics (`memoryBytes` / `cpuPercent` / `diskBytes`) are omitted by the
 * backend unless already produced (Non-Goal, Req 21.4), so they are optional and
 * usually absent. `unavailable` lists field names we expose but have no data for
 * (e.g. `dbQueryTimeMs`); the UI renders an explicit "unavailable" indicator for
 * those (Req 6.7). `cacheHitRatio` is a fraction in `[0.0, 1.0]`. */
export interface PerformanceSignals {
  routeClasses: RouteClassLatency[];
  topSlowRoutes: RouteClassLatency[];
  topSlowJobs: SlowJob[];
  dbQueryTimeMs?: number | null;
  /** Dashboard cache hit ratio in `[0.0, 1.0]`; `0.0` is a valid reading. */
  cacheHitRatio?: number | null;
  /** Number of observations used to compute `cacheHitRatio`. */
  cacheObservationCount: number;
  /** Aggregate population represented by these performance signals. */
  dataScope: string;
  memoryBytes?: number | null;
  cpuPercent?: number | null;
  diskBytes?: number | null;
  /** Field names that are exposed but have no data source yet (Req 6.7). */
  unavailable: string[];
  computedAt: string;
}

// ---------------------------------------------------------------------------
// Observability: Storage panel (Req 7) - mirrors backend `StoragePanel`.
//
// A cheap, cached storage snapshot: an approximate DB size + object-storage
// usage (each optionally stale - read from a periodic sample, never live-queried
// on request), the resource counts (avatars / resumes / resume versions), a
// coarse retention status string, and an estimated daily growth. Size, count,
// and growth availability are explicit so the UI never substitutes a misleading
// zero; `snapshotAt` identifies the cached count snapshot when one exists.
// ---------------------------------------------------------------------------

/** `GET /admin/storage` - cached DB size + object storage + counts + growth (Req 7). */
export interface StoragePanel {
  /** Approximate database size in bytes; `null` when no sample is available. */
  dbSizeBytes?: number | null;
  /** `true` when the DB-size reading is from a stale sample. */
  dbSizeStale: boolean;
  /** Object-storage usage in bytes; `null` when unavailable. */
  objectStorageBytes?: number | null;
  /** `true` when the object-storage reading is from a stale sample. */
  objectStorageStale: boolean;
  avatarCount: number;
  resumeCount: number;
  resumeVersionCount: number;
  /** `true` when resource counts could not be read. */
  countsUnavailable: boolean;
  /** `true` when resource counts came from a stale snapshot. */
  countsStale: boolean;
  /** Timestamp of the cached resource-count snapshot, if available. */
  snapshotAt?: string | null;
  /** Coarse retention status text (e.g. "healthy", "backlog"); `null` when unknown. */
  retentionStatus?: string | null;
  /** Estimated growth in bytes/day; `null` when there are insufficient samples. */
  growthBytesPerDay?: number | null;
  /** `true` when the growth estimate could not be computed. */
  growthUnavailable: boolean;
  /** Why the growth estimate is unavailable (e.g. "insufficient samples"). */
  growthUnavailableReason?: string | null;
  computedAt: string;
}

// ---------------------------------------------------------------------------
// Observability: Security view (Req 9) - mirrors backend `SecurityView`.
//
// An explicitly bounded aggregate of security-relevant counts read directly
// from indexed audit events over the exact trailing 24-hour interval: failed
// logins, current-role admin logins, authorization denials, rate-limit denials,
// and CAPTCHA enforcement denials. Counts are zero (never null) when no matching
// audit event exists. Aggregate-only and secret-free - never a raw event, IP,
// actor id, or log line.
// ---------------------------------------------------------------------------

/** `GET /admin/security` - bounded security aggregate counts (Req 9). */
export interface SecurityView {
  /** Nominal window size in hours. */
  windowHours: number;
  /** Exact UTC interval boundaries and their aggregation semantics. */
  windowStart: string;
  windowEnd: string;
  windowKind: string;
  /** Basis used to classify an authentication as an admin login. */
  adminLoginRoleBasis: string;
  loginFailed: number;
  adminLogin: number;
  authzDenied: number;
  rateLimited: number;
  suspicious: number;
  /** camelCase field names with no durable source - render "Not instrumented"
   *  instead of a misleading 0 for these (never silently show a fake zero). */
  notInstrumented: string[];
  computedAt: string;
}

// ---------------------------------------------------------------------------
// Observability: Read-only Configuration Diagnostics (Req 10) - mirrors the
// backend `ConfigDiagnostics`. Strictly READ-ONLY and secret-free: every
// configured secret is represented ONLY as a boolean presence indicator
// (`configured`), never a value. No mutation method exists on this endpoint.
// ---------------------------------------------------------------------------

/** `GET /admin/config` - secret-free, read-only configuration snapshot (Req 10). */
export interface ConfigDiagnostics {
  env: string;
  /** Compatibility list; `providersSource` explains how it was derived. */
  activeAiProviders: string[];
  providersSource: string;
  storageProvider: string;
  emailProvider: string;
  featureFlags: Record<string, boolean>;
  maintenanceMode: boolean;
  schedulerMode: string;
  gracePeriodDays: number;
  killSwitches: Record<string, boolean>;
  versions: Record<string, string>;
  /** Presence booleans only - `true` when a non-empty secret is configured. Never a value. */
  configured: Record<string, boolean>;
  computedAt: string;
}

// ---------------------------------------------------------------------------
// Observability: Overview KPIs (Req 13) - mirrors backend `OverviewKpis`.
//
// A handful of headline KPI cards assembled server-side from the existing O(1)
// snapshots (totals snapshot + durable Metric_Keys + in-process gauges). Each
// KPI is a `KpiValue`: a numeric `value` plus an explicit `unavailable` marker,
// so a source that cannot be computed degrades to an explicit "Unavailable"
// indicator (Req 13.10) instead of a misleading zero. `errorRate24h.value` is a
// percentage bounded 0.00-100.00, and `errorRateWindowLabel` states the actual
// date-bucket semantics; the count KPIs are non-negative integers.
// ---------------------------------------------------------------------------

/** One KPI card value + an explicit unavailability marker (Req 13.7/13.10). */
export interface KpiValue {
  /** The numeric value; `null` (with `unavailable: true`) when uncomputable. */
  value?: number | null;
  /** `true` when the underlying snapshot could not be computed. */
  unavailable: boolean;
}

/** `GET /admin/kpis` - Overview KPI cards from existing snapshots (Req 13). */
export interface OverviewKpis {
  totalUsers: KpiValue;
  newUsersToday: KpiValue;
  aiCallsToday: KpiValue;
  /** Percentage bounded 0.00-100.00 (two decimal places). */
  errorRate24h: KpiValue;
  /** Label describing the date buckets used for `errorRate24h`. */
  errorRateWindowLabel: string;
  purgeBacklog: KpiValue;
  computedAt: string;
  stale: boolean;
}

// ---------------------------------------------------------------------------
// Observability: Maintenance actions (Req 18) - the ONLY writes in the
// observability context. Each re-invokes an existing single-flighted job under
// `admin.manage`, audited + rate-limited + CSRF-protected. A held lock yields
// `already_running`; a disabled action yields `disabled` (Req 18.3/18.4/18.5).
// ---------------------------------------------------------------------------

/** The fixed, safe, idempotent set of maintenance actions (Req 18.5). */
export type MaintenanceAction = 'refresh-metrics' | 'run-rollup' | 'run-cleanup' | 'run-retention';

/** `POST /admin/maintenance/{action}` - mirrors the backend `MaintenanceResult`. */
export interface MaintenanceResult {
  status: 'started' | 'already_running' | 'disabled';
  action: string;
}

export interface UserListParams {
  cursor?: string | null;
  q?: string;
  status?: AdminUserStatus | '';
  role?: AdminUserRole | '';
  verified?: boolean;
  deleted?: boolean;
  limit?: number;
}

export interface AuditListParams {
  cursor?: string | null;
  event?: string;
  actor?: string;
  target?: string;
  from?: string;
  to?: string;
  limit?: number;
}

/** Thrown on any non-2xx admin response, carrying the backend machine `code`. */
export class AdminApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: unknown;
  readonly retryAfter?: number;

  constructor(
    code: string,
    message: string,
    status: number,
    details?: unknown,
    retryAfter?: number
  ) {
    super(message);
    this.name = 'AdminApiError';
    this.code = code;
    this.status = status;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

async function toError(response: Response): Promise<AdminApiError> {
  let code = 'error';
  let message = 'Something went wrong. Please try again.';
  let details: unknown;
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: unknown };
      detail?: string;
    };
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    } else if (typeof body?.detail === 'string') {
      code = body.detail;
    }
  } catch {
    /* non-JSON body - keep the generic message */
  }
  const retryHeader = response.headers.get('Retry-After');
  const retryAfter = retryHeader ? Number(retryHeader) : undefined;
  return new AdminApiError(
    code,
    message,
    response.status,
    details,
    Number.isFinite(retryAfter) ? retryAfter : undefined
  );
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    sp.set(key, String(value));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export async function getStats(): Promise<AdminStats> {
  return json<AdminStats>(await apiFetch('/admin/stats'));
}

export async function getUsageSeries(
  metric: MetricName,
  window: MetricWindow = 30
): Promise<UsageSeries> {
  return json<UsageSeries>(await apiFetch(`/admin/usage-series${qs({ metric, window })}`));
}

/** Observability: compose the System Health snapshot (Req 3). */
export async function getSystemHealth(): Promise<AdminHealth> {
  return json<AdminHealth>(await apiFetch('/admin/health'));
}

/** Observability: background-jobs panel - per-job state + gauges (Req 8). */
export async function getJobs(): Promise<JobsPanel> {
  return json<JobsPanel>(await apiFetch('/admin/jobs'));
}

/** Observability: read-only, secret-free configuration diagnostics (Req 10). */
export async function getConfig(): Promise<ConfigDiagnostics> {
  return json<ConfigDiagnostics>(await apiFetch('/admin/config'));
}

/** Observability: AI analytics for the trailing `window` days (1-365, default 30, Req 4). */
export async function getAiAnalytics(window: number = 30): Promise<AiAnalytics> {
  return json<AiAnalytics>(await apiFetch(`/admin/ai-analytics${qs({ window })}`));
}

/** Observability: grouped errors summary for a fixed window (7/30/90, default 30, Req 5). */
export async function getErrors(window: MetricWindow = 30): Promise<ErrorsSummary> {
  return json<ErrorsSummary>(await apiFetch(`/admin/errors${qs({ window })}`));
}

/** Observability: performance signals - latency/cache aggregates, no params (Req 6). */
export async function getPerformance(): Promise<PerformanceSignals> {
  return json<PerformanceSignals>(await apiFetch('/admin/performance'));
}

/** Observability: storage panel - cached DB/object-storage size + counts + growth (Req 7). */
export async function getStorage(): Promise<StoragePanel> {
  return json<StoragePanel>(await apiFetch('/admin/storage'));
}

/** Observability: security view - trailing-24h aggregate counts, no params (Req 9). */
export async function getSecurity(): Promise<SecurityView> {
  return json<SecurityView>(await apiFetch('/admin/security'));
}

/** Observability: Overview KPI cards - headline metrics from O(1) snapshots (Req 13). */
export async function getKpis(): Promise<OverviewKpis> {
  return json<OverviewKpis>(await apiFetch('/admin/kpis'));
}

export async function listUsers(params: UserListParams = {}): Promise<AdminUserList> {
  return json<AdminUserList>(
    await apiFetch(
      `/admin/users${qs({
        cursor: params.cursor,
        q: params.q,
        status: params.status,
        role: params.role,
        verified: params.verified,
        deleted: params.deleted,
        limit: params.limit,
      })}`
    )
  );
}

export async function getUserDetail(id: string): Promise<AdminUserDetail> {
  return json<AdminUserDetail>(await apiFetch(`/admin/users/${encodeURIComponent(id)}`));
}

export async function listAudit(params: AuditListParams = {}): Promise<AuditList> {
  return json<AuditList>(
    await apiFetch(
      `/admin/audit${qs({
        cursor: params.cursor,
        event: params.event,
        actor: params.actor,
        target: params.target,
        from: params.from,
        to: params.to,
        limit: params.limit,
      })}`
    )
  );
}

/** Cursor-paginated, privacy-minimized user error reports. */
export async function listErrorReports(
  params: ErrorReportListParams = {}
): Promise<AdminErrorReportList> {
  return json<AdminErrorReportList>(
    await apiFetch(`/admin/error-reports${qs({ cursor: params.cursor, limit: params.limit })}`)
  );
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export async function setUserStatus(
  id: string,
  status: 'active' | 'disabled'
): Promise<MutationResult> {
  const action = status === 'disabled' ? 'disable' : 'enable';
  return json<MutationResult>(
    await apiPost(`/admin/users/${encodeURIComponent(id)}/${action}`, {})
  );
}

export async function setUserRole(id: string, role: AdminUserRole): Promise<MutationResult> {
  return json<MutationResult>(await apiPatch(`/admin/users/${encodeURIComponent(id)}`, { role }));
}

export async function deleteUser(id: string, email: string): Promise<MutationResult> {
  return json<MutationResult>(
    await apiPost(`/admin/users/${encodeURIComponent(id)}/delete`, { email })
  );
}

export async function restoreUser(id: string): Promise<MutationResult> {
  return json<MutationResult>(await apiPost(`/admin/users/${encodeURIComponent(id)}/restore`, {}));
}

export async function bulkDisable(ids: string[]): Promise<BulkDisableResult> {
  return json<BulkDisableResult>(await apiPost('/admin/users/bulk-disable', { ids }));
}

// ---------------------------------------------------------------------------
// Admin invites (secure admin signup - Option B)
// ---------------------------------------------------------------------------

/** List bounded recent admin-invite lifecycle history. Never returns a token. */
export async function listInvites(): Promise<AdminInviteList> {
  return json<AdminInviteList>(await apiFetch('/admin/invites'));
}

/** Issue an email-bound, single-use admin invite; returns the shareable URL
 *  (shown only once). `ttlHours` optionally overrides the configured default. */
export async function createInvite(email: string, ttlHours?: number): Promise<CreatedInvite> {
  return json<CreatedInvite>(
    await apiPost('/admin/invites', {
      email,
      ...(typeof ttlHours === 'number' ? { ttlHours } : {}),
    })
  );
}

/** Revoke an outstanding invite by id (idempotent). */
export async function revokeInvite(id: string): Promise<MutationResult> {
  return json<MutationResult>(await apiDelete(`/admin/invites/${encodeURIComponent(id)}`));
}

/**
 * Observability maintenance write (Req 18) - re-invoke one of the fixed, safe,
 * idempotent jobs. `admin.manage` + CSRF are enforced server-side; the CSRF
 * double-submit token is injected automatically by {@link apiPost}. Returns the
 * dispatch outcome (`started` / `already_running` / `disabled`) - never job
 * output. The action is path-segment-only from a closed union, so there is no
 * user-supplied string to escape.
 */
export async function runMaintenance(action: MaintenanceAction): Promise<MaintenanceResult> {
  return json<MaintenanceResult>(await apiPost(`/admin/maintenance/${action}`, {}));
}

/** The typed admin surface consumed by the feature hooks. */
export const adminApi = {
  getStats,
  getUsageSeries,
  listUsers,
  getUserDetail,
  listAudit,
  listErrorReports,
  setUserStatus,
  setUserRole,
  deleteUser,
  restoreUser,
  bulkDisable,
};

export type AdminApi = typeof adminApi;

// ---------------------------------------------------------------------------
// Bounded-context groupings (Req 19, Req 11.4)
//
// The admin panel is split into two independent bounded contexts that never
// share mutable state. The typed client mirrors that split so each page/hook
// imports from the namespace that matches its context:
//
//   - `observabilityApi` - operational health of the platform: Health, Errors,
//     Storage, Jobs, AI usage/cost, Security, Performance, Config, Overview KPIs
//     and the Maintenance writes.
//   - `analyticsApi` - product analytics: feature usage, resume analytics and
//     user growth.
//
// All endpoint methods below reuse the shared helpers (`apiFetch`, `json`, `qs`,
// `AdminApiError`, and the mutation helpers); no request machinery is duplicated.
//
// Back-compat: the flat `adminApi` export is intentionally left unchanged so the
// existing user-management/audit/stats hooks keep working.
// ---------------------------------------------------------------------------

/**
 * Observability context client.
 *
 * Re-exposes the complete operational read surface and the bounded maintenance
 * write surface under one correctly scoped namespace. `getStats` and
 * `getUsageSeries` remain on {@link adminApi} for backward compatibility.
 */
export const observabilityApi = {
  getStats,
  getUsageSeries,
  getSystemHealth,
  getJobs,
  getConfig,
  getAiAnalytics,
  getErrors,
  getPerformance,
  getStorage,
  getSecurity,
  getKpis,
  runMaintenance,
};

export type ObservabilityApi = typeof observabilityApi;

// ---------------------------------------------------------------------------
// Product Analytics: Feature Usage (Req 16, task 16.3)
// ---------------------------------------------------------------------------

/** One data point in a feature-usage time series. */
export interface FeatureSeriesPoint {
  date: string;
  value: number;
}

/** Usage series for a single feature over the requested window. */
export interface FeatureSeries {
  feature: string;
  points: FeatureSeriesPoint[];
  total: number;
}

/** `GET /admin/analytics/feature-usage?window=` - feature invocations over time. */
export interface FeatureUsage {
  window: number;
  series: FeatureSeries[];
  computedAt: string;
}

/** Fetch feature-usage analytics for the given time window (default 30 days). */
export async function getFeatureUsage(window: MetricWindow = 30): Promise<FeatureUsage> {
  return json<FeatureUsage>(await apiFetch(`/admin/analytics/feature-usage${qs({ window })}`));
}

// ---------------------------------------------------------------------------
// Product Analytics: Resume Analytics (Req 14, task 17.3)
//
// Current source split (generated / imported / tailored) with counts and
// percentages, up to ten popular templates, explicit window deletion/net-change
// totals, and snapshot timestamps. Reuses the shared {@link SeriesPoint} for the
// daily net inventory change (matching backend `list[SeriesPoint]`).
// ---------------------------------------------------------------------------

/** Current resume inventory split: counts + percentages for each origin. */
export interface ResumeSourceSplit {
  generated: number;
  imported: number;
  tailored: number;
  generatedPct: number;
  importedPct: number;
  tailoredPct: number;
}

/** One popular-template row (name + usage count). */
export interface TemplateCount {
  name: string;
  count: number;
}

/** `GET /admin/analytics/resumes?window=` - inventory and window activity. */
export interface ResumeAnalytics {
  window: number;
  sourceSplit: ResumeSourceSplit;
  /** Up to ten most-used templates (ties broken by name). */
  topTemplates: TemplateCount[];
  deletedInWindow: number;
  netChange: number;
  inventoryAsOf: string;
  templatesAsOf: string;
  /** Daily net inventory change over the window (reuses {@link SeriesPoint}). */
  growth: SeriesPoint[];
  computedAt: string;
}

/** Fetch resume analytics for the given time window (default 30 days). */
export async function getResumeAnalytics(window: MetricWindow = 30): Promise<ResumeAnalytics> {
  return json<ResumeAnalytics>(await apiFetch(`/admin/analytics/resumes${qs({ window })}`));
}

/**
 * Product Analytics context client.
 *
 * Contains `getFeatureUsage` (Req 16, task 16.3) and `getResumeAnalytics`
 * (Req 14, task 17.3).
 */
export const analyticsApi = {
  getFeatureUsage,
  getResumeAnalytics,
};

export type AnalyticsApi = typeof analyticsApi;
