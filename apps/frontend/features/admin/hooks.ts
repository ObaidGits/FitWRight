'use client';

/**
 * Admin data hooks (Task 8.1/8.3) - typed query keys + correct invalidation.
 *
 * - Queries: stats, usage-series, users (keyset paginated), user detail, audit.
 * - Mutations invalidate the user list + detail + stats so the UI reflects
 *   server truth. Status toggles are **optimistic with rollback** (reversible);
 *   **delete is pessimistic** (await the server) per R10.4.
 * - All errors surface as {@link AdminApiError} with a machine `code` the UI
 *   maps to messaging that mirrors the server (last-active-admin, self-action).
 */
import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import {
  adminApi,
  analyticsApi,
  observabilityApi,
  type AdminUserList,
  type AdminUserRow,
  type AuditListParams,
  type MaintenanceAction,
  type MetricName,
  type MetricWindow,
  type UserListParams,
} from '@/lib/api/admin';

// ---------------------------------------------------------------------------
// Typed query keys (single source of truth for invalidation)
// ---------------------------------------------------------------------------

export const adminKeys = {
  all: ['admin'] as const,
  stats: () => [...adminKeys.all, 'stats'] as const,
  series: (metric: MetricName, window: MetricWindow) =>
    [...adminKeys.all, 'series', metric, window] as const,
  users: (params: UserListParams) => [...adminKeys.all, 'users', params] as const,
  userDetail: (id: string) => [...adminKeys.all, 'user', id] as const,
  audit: (params: AuditListParams) => [...adminKeys.all, 'audit', params] as const,
  health: () => [...adminKeys.all, 'health'] as const,
  jobs: () => [...adminKeys.all, 'jobs'] as const,
  config: () => [...adminKeys.all, 'config'] as const,
  aiAnalytics: (window: number) => [...adminKeys.all, 'ai-analytics', window] as const,
  errors: (window: number) => [...adminKeys.all, 'errors', window] as const,
  performance: () => [...adminKeys.all, 'performance'] as const,
  storage: () => [...adminKeys.all, 'storage'] as const,
  security: () => [...adminKeys.all, 'security'] as const,
  kpis: () => [...adminKeys.all, 'kpis'] as const,
  featureUsage: (window: number) => [...adminKeys.all, 'feature-usage', window] as const,
  resumeAnalytics: (window: number) => [...adminKeys.all, 'resume-analytics', window] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export function useAdminStats() {
  return useQuery({ queryKey: adminKeys.stats(), queryFn: adminApi.getStats });
}

export function useUsageSeries(metric: MetricName, window: MetricWindow = 30) {
  return useQuery({
    queryKey: adminKeys.series(metric, window),
    queryFn: () => adminApi.getUsageSeries(metric, window),
  });
}

export function useAdminUsers(params: UserListParams) {
  return useQuery({
    queryKey: adminKeys.users(params),
    queryFn: () => adminApi.listUsers(params),
    placeholderData: keepPreviousData,
  });
}

export function useAdminUserDetail(id: string | null) {
  return useQuery({
    queryKey: adminKeys.userDetail(id ?? '-'),
    queryFn: () => adminApi.getUserDetail(id as string),
    enabled: !!id,
  });
}

export function useAdminAudit(params: AuditListParams) {
  return useQuery({
    queryKey: adminKeys.audit(params),
    queryFn: () => adminApi.listAudit(params),
    placeholderData: keepPreviousData,
  });
}

/**
 * System Health snapshot (Req 3, task 6.4).
 *
 * Reads from the observability-context client. Composed server-side from
 * `/health/ready`, `/status`, gauges, Alembic head-vs-applied and release
 * fields - the hook simply surfaces `isLoading` / `isError` / `refetch` so the
 * page can render an explicit loading state and an error state with a working
 * retry control (Req 3.9).
 */
export function useSystemHealth() {
  return useQuery({
    queryKey: adminKeys.health(),
    queryFn: observabilityApi.getSystemHealth,
  });
}

/**
 * Background-jobs panel (Req 8, task 7.2).
 *
 * A separate observability query from {@link useSystemHealth} so the jobs table
 * can load, error and refresh independently of the health tiles. Surfaces
 * `isLoading` / `isError` / `refetch` so the Health page can render an explicit
 * loading state and an error state with a working retry control.
 */
export function useJobs() {
  return useQuery({
    queryKey: adminKeys.jobs(),
    queryFn: observabilityApi.getJobs,
  });
}

/**
 * Read-only configuration diagnostics (Req 10, task 8.3).
 *
 * A standalone observability query so the read-only Config tab on the Health
 * page loads, errors and refreshes independently of the health tiles/jobs.
 * The payload is secret-free (presence booleans only); this hook never triggers
 * a write - configuration is strictly read-only (Req 10.3 / 11.4). Surfaces
 * `isLoading` / `isError` / `refetch` for the tab's loading/error/retry states.
 */
export function useConfig() {
  return useQuery({
    queryKey: adminKeys.config(),
    queryFn: observabilityApi.getConfig,
  });
}

/**
 * AI analytics for a trailing `window` (Req 4, task 9.4).
 *
 * A standalone observability query so the AI page loads, errors and refreshes
 * independently. Mirrors {@link useSystemHealth}: surfaces `isLoading` /
 * `isError` / `refetch` for the page's loading/error/retry states. The `window`
 * is part of the query key so changing it (7 / 30 / 90) refetches the series
 * (default 30). The backend validates the 1-365 range server-side.
 */
export function useAiAnalytics(window = 30) {
  return useQuery({
    queryKey: adminKeys.aiAnalytics(window),
    queryFn: () => observabilityApi.getAiAnalytics(window),
  });
}

/**
 * Grouped errors summary for a trailing `window` (Req 5, task 10.2).
 *
 * A standalone observability query so the Errors card on the Health page loads,
 * errors and refreshes independently of the health tiles/jobs/config. Mirrors
 * {@link useAiAnalytics}: surfaces `isLoading` / `isError` / `refetch` for the
 * card's loading/error/retry states. The `window` (7 / 30 / 90, default 30) is
 * part of the query key so changing it refetches the summary; the backend
 * rejects any other window with a 400 `invalid_window`. Grouped buckets only -
 * never raw logs/stacks/traces (Req 21.2).
 */
export function useErrors(window: MetricWindow = 30) {
  return useQuery({
    queryKey: adminKeys.errors(window),
    queryFn: () => observabilityApi.getErrors(window),
  });
}

/**
 * Performance signals (Req 6, task 11.2).
 *
 * A standalone observability query so the Performance card on the Health page
 * loads, errors and refreshes independently of the health tiles/jobs/errors.
 * Mirrors {@link useErrors}: surfaces `isLoading` / `isError` / `refetch` for the
 * card's loading/error/retry states. Takes no window - the endpoint reads only
 * from aggregates the backend already produces (O(1), no new instrumentation).
 */
export function usePerformance() {
  return useQuery({
    queryKey: adminKeys.performance(),
    queryFn: observabilityApi.getPerformance,
  });
}

/**
 * Storage panel (Req 7, task 12.3).
 *
 * A standalone observability query so the Storage page loads, errors and
 * refreshes independently. Mirrors {@link usePerformance}: surfaces `isLoading` /
 * `isError` / `refetch` for the page's loading/error/retry states. Takes no
 * params - the endpoint returns a cheap, cached snapshot (DB/object-storage
 * size samples, counts and an estimated growth), never a live-queried size.
 * Size/growth fields can be `null` with paired `*Stale` / `growthUnavailable`
 * markers so the UI degrades to an explicit indicator instead of a bogus zero.
 */
export function useStorage() {
  return useQuery({
    queryKey: adminKeys.storage(),
    queryFn: observabilityApi.getStorage,
  });
}

/**
 * Security view (Req 9, task 13.3).
 *
 * A standalone observability query so the compact security strip on the Audit
 * page loads, errors and refreshes independently of the audit list. Mirrors
 * {@link useStorage}: surfaces `isLoading` / `isError` / `refetch` so the strip
 * renders its own loading/error/retry states without blocking the audit list.
 * Takes no params - the endpoint returns a cheap, trailing-24h aggregate of
 * security counts (failed logins, admin logins, authz denials, rate-limited,
 * suspicious) read from the `SEC_*` aggregates; counts are zero (never null)
 * when there is no data for the window.
 */
export function useSecurity() {
  return useQuery({
    queryKey: adminKeys.security(),
    queryFn: observabilityApi.getSecurity,
  });
}

/**
 * Overview KPI cards (Req 13, task 14.2/14.3).
 *
 * A standalone observability query so the Overview KPI strip loads, errors and
 * refreshes independently of the windowed usage chart. Surfaces `isLoading` /
 * `isError` / `refetch` for the page's loading/error/retry states, plus
 * `dataUpdatedAt` / `data.stale` so the page can render a staleness indicator
 * when the snapshot age exceeds 60s (Req 13.9) and clear it on refresh (Req
 * 11.10). Each KPI is a `KpiValue` carrying an explicit `unavailable` marker so
 * a card degrades to an "Unavailable" indicator rather than a bogus zero (Req
 * 13.10). Takes no params - every KPI is an O(1) read from existing snapshots.
 */
export function useKpis() {
  return useQuery({
    queryKey: adminKeys.kpis(),
    queryFn: observabilityApi.getKpis,
  });
}

/**
 * Feature-usage analytics for a trailing `window` (Req 16, 19.1, task 16.3).
 *
 * Reads from the PRODUCT-ANALYTICS context client ({@link analyticsApi}) - a
 * bounded context kept distinct from observability so product-adoption metrics
 * never share mutable state with operational health. Powers the "Product usage"
 * section on the Overview page. The `window` (7 / 30 / 90, default 30) is part
 * of the query key so changing it refetches the series; `keepPreviousData`
 * avoids a loading flash on window changes. Surfaces `isLoading` / `isError` /
 * `refetch` for the section's loading/error/retry states.
 */
export function useFeatureUsage(window: MetricWindow = 30) {
  return useQuery({
    queryKey: adminKeys.featureUsage(window),
    queryFn: () => analyticsApi.getFeatureUsage(window),
    placeholderData: keepPreviousData,
  });
}

/**
 * Resume analytics for a trailing `window` (Req 14, 19.1, task 17.3).
 *
 * Reads from the PRODUCT-ANALYTICS context client ({@link analyticsApi}) - the
 * same bounded context as {@link useFeatureUsage}, kept distinct from
 * observability. Powers the resume-analytics block in the "Product usage"
 * section on the Overview page (source split + popular templates + growth). The
 * `window` (7 / 30 / 90, default 30) is part of the query key so changing it
 * refetches; `keepPreviousData` avoids a loading flash on window changes.
 * Surfaces `isLoading` / `isError` / `refetch` for the block's states.
 */
export function useResumeAnalytics(window: MetricWindow = 30) {
  return useQuery({
    queryKey: adminKeys.resumeAnalytics(window),
    queryFn: () => analyticsApi.getResumeAnalytics(window),
    placeholderData: keepPreviousData,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Invalidate the user list + stats + audit (and one detail) after a mutation. */
function useInvalidateAdmin() {
  const qc = useQueryClient();
  return (userId?: string) => {
    qc.invalidateQueries({ queryKey: [...adminKeys.all, 'users'] });
    qc.invalidateQueries({ queryKey: adminKeys.stats() });
    qc.invalidateQueries({ queryKey: [...adminKeys.all, 'audit'] });
    if (userId) qc.invalidateQueries({ queryKey: adminKeys.userDetail(userId) });
  };
}

/**
 * Optimistic status toggle (reversible -> optimistic with rollback, R10.4).
 * Patches every cached user-list page + the detail immediately, then reconciles
 * with the server response (or rolls back on error).
 */
export function useSetUserStatus() {
  const qc = useQueryClient();
  const invalidate = useInvalidateAdmin();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'active' | 'disabled' }) =>
      adminApi.setUserStatus(id, status),
    onMutate: async ({ id, status }) => {
      await qc.cancelQueries({ queryKey: [...adminKeys.all, 'users'] });
      const snapshots = qc.getQueriesData<AdminUserList>({ queryKey: [...adminKeys.all, 'users'] });
      for (const [key, data] of snapshots) {
        if (!data) continue;
        qc.setQueryData<AdminUserList>(key, {
          ...data,
          items: data.items.map((u: AdminUserRow) => (u.id === id ? { ...u, status } : u)),
        });
      }
      return { snapshots };
    },
    onError: (_err, _vars, context) => {
      // Roll back every optimistically-patched page.
      context?.snapshots?.forEach(([key, data]) => qc.setQueryData(key, data));
    },
    onSettled: (_data, _err, vars) => invalidate(vars.id),
  });
}

/** Role change (revokes sessions server-side). Pessimistic; invalidate on success. */
export function useSetUserRole() {
  const invalidate = useInvalidateAdmin();
  return useMutation({
    mutationFn: ({ id, role }: { id: string; role: 'user' | 'admin' }) =>
      adminApi.setUserRole(id, role),
    onSuccess: (_data, vars) => invalidate(vars.id),
  });
}

/** Pessimistic delete (destructive -> await server, no optimistic UI, R10.4). */
export function useDeleteUser() {
  const invalidate = useInvalidateAdmin();
  return useMutation({
    mutationFn: ({ id, email }: { id: string; email: string }) => adminApi.deleteUser(id, email),
    onSuccess: (_data, vars) => invalidate(vars.id),
  });
}

export function useRestoreUser() {
  const invalidate = useInvalidateAdmin();
  return useMutation({
    mutationFn: (id: string) => adminApi.restoreUser(id),
    onSuccess: (_data, id) => invalidate(id),
  });
}

export function useBulkDisable() {
  const invalidate = useInvalidateAdmin();
  return useMutation({
    mutationFn: (ids: string[]) => adminApi.bulkDisable(ids),
    onSuccess: () => invalidate(),
  });
}

/**
 * Trigger a maintenance action (Req 18, task 8.3) - the only observability write.
 *
 * Re-invokes one of the four fixed, idempotent single-flighted jobs under
 * `admin.manage` (enforced + audited + rate-limited + CSRF-protected server-side).
 * On success it invalidates the health, jobs and stats queries so the panels
 * reflect the just-triggered job (e.g. a job flips to running / a metric snapshot
 * refreshes). The caller surfaces `isPending` (disable buttons while in flight)
 * and `data` (the `started | already_running | disabled` outcome) via an
 * `aria-live` region + toast.
 */
export function useRunMaintenance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: MaintenanceAction) => observabilityApi.runMaintenance(action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: adminKeys.health() });
      qc.invalidateQueries({ queryKey: adminKeys.jobs() });
      qc.invalidateQueries({ queryKey: adminKeys.stats() });
    },
  });
}
