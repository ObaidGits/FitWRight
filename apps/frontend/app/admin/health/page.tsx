'use client';

/**
 * Admin System Health (Task 6.4 / Req 3, 17).
 *
 * Renders the six subsystem tiles composed by the backend `HealthService`, the
 * secret-free release/deployment fields, and backend uptime. Every tile carries
 * BOTH a color AND a literal text status label so status is never conveyed by
 * color alone (Req 3.8, a11y). On fetch failure or timeout the page shows an
 * explicit error state with a working retry control (Req 3.9) - never a blank or
 * partial-without-indication view.
 *
 * This page renders ONLY what `AdminHealth` provides. It deliberately shows no
 * CPU / RAM / disk / host metrics (Non-Goals - Req 21.3 / 21.4).
 *
 * The background-jobs table (task 7.2, Req 3.4 / 8.4) is its OWN observability
 * query (`useJobs`) so it loads, errors and refreshes independently of the health
 * tiles. Each job's STATE (running / failed / completed / idle) is derived on the
 * frontend from the run markers and always paired with a text label (never color
 * alone, a11y). The panel gauges (queue length, purge backlog) distinguish an
 * explicit "unavailable" from a real zero.
 */
import * as React from 'react';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import CheckCircle from 'lucide-react/dist/esm/icons/circle-check';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import XCircle from 'lucide-react/dist/esm/icons/circle-x';
import Circle from 'lucide-react/dist/esm/icons/circle';
import Loader from 'lucide-react/dist/esm/icons/loader-circle';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/atelier/table';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/atelier/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import { useToast } from '@/components/atelier/toast';
import { UsageChart } from '@/components/admin/mini-chart';
import { LocalTime } from '@/components/admin/local-time';
import { useSession } from '@/lib/context/session';
import {
  useSystemHealth,
  useJobs,
  useConfig,
  useErrors,
  usePerformance,
  useRunMaintenance,
} from '@/features/admin/hooks';
import type {
  ConfigDiagnostics,
  ErrorsSummary,
  HealthStatus,
  HealthTile,
  JobRow,
  MaintenanceAction,
  MetricWindow,
  PerformanceSignals,
  ReleaseInfo,
  RouteClassLatency,
  SlowJob,
} from '@/lib/api/admin';

/**
 * Presentation for each traffic-light status. Color is paired with an icon AND
 * a text label everywhere so status is never signalled by color alone (Req 3.8).
 */
const STATUS_PRESENTATION: Record<
  HealthStatus,
  {
    label: string;
    badge: React.ComponentProps<typeof Badge>['variant'];
    dot: string;
    icon: React.ComponentType<{ className?: string }>;
  }
> = {
  ok: { label: 'OK', badge: 'success', dot: 'bg-[var(--at-success)]', icon: CheckCircle },
  degraded: {
    label: 'Degraded',
    badge: 'warning',
    dot: 'bg-[var(--at-warning)]',
    icon: AlertTriangle,
  },
  down: { label: 'Down', badge: 'danger', dot: 'bg-[var(--destructive)]', icon: XCircle },
};

function TileCard({ tile }: { tile: HealthTile }) {
  const presentation = STATUS_PRESENTATION[tile.status] ?? STATUS_PRESENTATION.down;
  const Icon = presentation.icon;
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          {/* Color dot - decorative; the adjacent text label is authoritative. */}
          <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${presentation.dot}`} aria-hidden />
          <span className="text-sm font-semibold">{tile.name}</span>
        </div>
        {/* Text status label in addition to color (Req 3.8). */}
        <Badge variant={presentation.badge} aria-label={`Status: ${presentation.label}`}>
          <Icon className="h-3.5 w-3.5" aria-hidden />
          {presentation.label}
        </Badge>
      </div>
      {tile.detail && <p className="mt-3 text-sm text-[var(--muted-foreground)]">{tile.detail}</p>}
    </Card>
  );
}

function ReleaseField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <dt className="text-[var(--muted-foreground)]">{label}</dt>
      <dd className="text-right font-medium">{value ?? '-'}</dd>
    </div>
  );
}

function formatUptime(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return '-';
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const mins = Math.floor((total % 3_600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  parts.push(`${mins}m`);
  return parts.join(' ');
}

function ReleaseCard({
  release,
  uptimeSeconds,
}: {
  release: ReleaseInfo;
  uptimeSeconds?: number | null;
}) {
  const migrationMismatch =
    !!release.migrationApplied &&
    !!release.migrationHead &&
    release.migrationApplied !== release.migrationHead;

  return (
    <Card className="p-5">
      <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">Release</h2>
      <dl className="divide-y divide-[var(--border)] text-sm">
        <ReleaseField label="Version" value={release.version} />
        <ReleaseField label="Environment" value={release.env} />
        <ReleaseField label="Build" value={release.build} />
        <ReleaseField
          label="Commit"
          value={release.commit ? <span className="font-mono text-xs">{release.commit}</span> : '-'}
        />
        <ReleaseField
          label="Migration (applied)"
          value={
            release.migrationApplied ? (
              <span className="inline-flex items-center gap-2">
                <span className="font-mono text-xs">{release.migrationApplied}</span>
                {migrationMismatch && (
                  <Badge variant="warning" aria-label="Applied migration differs from head">
                    behind head
                  </Badge>
                )}
              </span>
            ) : (
              '-'
            )
          }
        />
        <ReleaseField
          label="Migration (head)"
          value={
            release.migrationHead ? (
              <span className="font-mono text-xs">{release.migrationHead}</span>
            ) : (
              '-'
            )
          }
        />
        <ReleaseField label="Backend uptime" value={formatUptime(uptimeSeconds)} />
      </dl>
    </Card>
  );
}

/**
 * The Health page presents its content across two tabs (Req 11.4): an
 * **Overview** tab (subsystem tiles + release + background jobs) and a
 * read-only **Configuration** tab (config diagnostics + a manage-only
 * Maintenance panel). Tabs are the Atelier/Radix primitive, so every trigger is
 * reachable and operable by keyboard alone (Req 11.11). Each tab's data is a
 * separate query that loads, errors and refreshes independently.
 */
export default function AdminHealthPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">System Health</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Subsystem status, background jobs, and configuration diagnostics.
        </p>
      </div>

      <Tabs defaultValue="overview">
        <TabsList aria-label="Health sections">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="configuration">Configuration</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-6">
          <HealthOverview />
          {/* Grouped errors summary - its own independent query (Req 5). */}
          <ErrorsCard />
          {/* Performance signals - its own independent query (Req 6). */}
          <PerformanceCard />
          {/* Background-jobs table - its own independent query (Req 3.4 / 8.4). */}
          <JobsCard />
        </TabsContent>

        <TabsContent value="configuration" className="space-y-6">
          {/* Read-only diagnostics (Req 11.4) - no edit/save/delete controls. */}
          <ConfigTab />
          {/* Maintenance writes - rendered ONLY to manage-capable admins (Req 11.6). */}
          <MaintenancePanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** Overview tab body: the six subsystem tiles + release/uptime (Req 3, 17). */
function HealthOverview() {
  const health = useSystemHealth();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
          {health.data ? (
            <>
              As of <LocalTime iso={health.data.computedAt} />
              {health.data.stale && (
                <Badge variant="danger" aria-label="Data may be stale">
                  Stale
                </Badge>
              )}
            </>
          ) : (
            'Subsystem status at a glance.'
          )}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => health.refetch()}
          disabled={health.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${health.isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {health.isError ? (
        // Explicit error state with a retry control (Req 3.9) - never blank/partial.
        <ErrorState
          title="Couldn't load system health"
          description={(health.error as Error)?.message}
          onRetry={() => health.refetch()}
        />
      ) : health.isLoading || !health.data ? (
        // Explicit loading state (Req 11.15).
        <LoadingSkeleton rows={3} />
      ) : (
        <>
          {/* Six subsystem tiles: color + text status label (Req 3.8). */}
          <section aria-label="Subsystem health tiles">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {health.data.tiles.map((tile) => (
                <TileCard key={tile.name} tile={tile} />
              ))}
            </div>
          </section>

          {/* Release / deployment fields + backend uptime. */}
          <ReleaseCard
            release={health.data.release}
            uptimeSeconds={health.data.backendUptimeSeconds}
          />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Background jobs (Req 8) - separate observability query from the health tiles.
// ---------------------------------------------------------------------------

/** Frontend-derived lifecycle state of one job (never sent by the backend). */
type JobState = 'running' | 'failed' | 'completed' | 'idle';

/**
 * Derive a job's state from its run markers (Req 8.4):
 * - running   <=> `runningSince` is set OR the single-flight lock is held;
 * - failed    <=> the last recorded outcome was a failure;
 * - completed <=> the last outcome was success/skipped and it is not running;
 * - idle      <=> never run (no outcome) and not running.
 * Running takes precedence so an in-flight run is never masked by a stale outcome.
 */
function deriveJobState(job: JobRow): JobState {
  if (job.runningSince != null || job.lockState === 'held') return 'running';
  if (job.lastOutcome === 'failure') return 'failed';
  if (job.lastOutcome === 'success' || job.lastOutcome === 'skipped') return 'completed';
  return 'idle';
}

/** State presentation: color + icon + a literal text label (never color alone). */
const JOB_STATE_PRESENTATION: Record<
  JobState,
  {
    label: string;
    badge: React.ComponentProps<typeof Badge>['variant'];
    icon: React.ComponentType<{ className?: string }>;
    spin?: boolean;
  }
> = {
  running: { label: 'Running', badge: 'ai', icon: Loader, spin: true },
  failed: { label: 'Failed', badge: 'danger', icon: XCircle },
  completed: { label: 'Completed', badge: 'success', icon: CheckCircle },
  idle: { label: 'Idle', badge: 'neutral', icon: Circle },
};

/** Human-friendly duration ("45s", "3m", "1h 5m", "2d 3h"). `-` when unknown. */
function formatDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return '-';
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const mins = Math.floor((total % 3_600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (mins) parts.push(`${mins}m`);
  return parts.join(' ') || '0m';
}

function JobStateBadges({ job, state }: { job: JobRow; state: JobState }) {
  const presentation = JOB_STATE_PRESENTATION[state];
  const Icon = presentation.icon;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge variant={presentation.badge} aria-label={`State: ${presentation.label}`}>
        <Icon className={`h-3.5 w-3.5 ${presentation.spin ? 'animate-spin' : ''}`} aria-hidden />
        {presentation.label}
      </Badge>
      {/* Stuck detection is advisory - surfaced as its own text+color badge. */}
      {job.potentiallyStuck && (
        <Badge variant="warning" aria-label="Potentially stuck">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
          Stuck?
        </Badge>
      )}
    </div>
  );
}

/** The lock's held/free state (or `-` when unknown), text + color. */
function LockCell({ lockState }: { lockState: JobRow['lockState'] }) {
  if (lockState === 'held')
    return (
      <Badge variant="warning" aria-label="Lock held">
        Held
      </Badge>
    );
  if (lockState === 'free') return <span className="text-[var(--muted-foreground)]">Free</span>;
  return <span className="text-[var(--muted-foreground)]">-</span>;
}

/** Current run duration (when running) with the expected duration as sub-text. */
function DurationCell({ job, state }: { job: JobRow; state: JobState }) {
  const running = state === 'running';
  return (
    <div className="flex flex-col leading-tight">
      {running ? (
        <span title={job.runningSince ? `running since ${job.runningSince}` : undefined}>
          {formatDuration(job.currentDurationSeconds)}
        </span>
      ) : (
        <span className="text-[var(--muted-foreground)]">-</span>
      )}
      <span className="text-xs text-[var(--muted-foreground)]">
        exp {formatDuration(job.expectedDurationSeconds)}
      </span>
    </div>
  );
}

/** One panel gauge: a value, or an explicit "Unavailable" badge (never a fake 0). */
function GaugeStat({
  label,
  value,
  unavailable,
}: {
  label: string;
  value?: number | null;
  unavailable: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      {unavailable ? (
        <Badge variant="outline" aria-label={`${label}: unavailable`}>
          Unavailable
        </Badge>
      ) : (
        <span className="font-medium">{value ?? '-'}</span>
      )}
    </div>
  );
}

function JobsCard() {
  const jobs = useJobs();

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Background jobs</h2>
          {jobs.data?.stale && (
            <Badge variant="danger" aria-label="Jobs data may be stale">
              Stale
            </Badge>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => jobs.refetch()}
          disabled={jobs.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${jobs.isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {jobs.isError ? (
        <ErrorState
          title="Couldn't load background jobs"
          description={(jobs.error as Error)?.message}
          onRetry={() => jobs.refetch()}
        />
      ) : jobs.isLoading || !jobs.data ? (
        <LoadingSkeleton rows={3} />
      ) : (
        <>
          {/* Panel-level gauges: worker-independent queue + purge backlog. */}
          <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <GaugeStat
              label="Queue length"
              value={jobs.data.queueLength}
              unavailable={jobs.data.queueLengthUnavailable}
            />
            <GaugeStat
              label="Purge backlog"
              value={jobs.data.purgeBacklog}
              unavailable={jobs.data.purgeBacklogUnavailable}
            />
          </div>

          {jobs.data.jobs.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              No background jobs reported.
            </p>
          ) : (
            <>
              {/* Desktop table (scroll container guards against overflow). */}
              <div className="hidden md:block">
                <Table>
                  <caption className="sr-only">
                    Background jobs status, last and next run, and lock state
                  </caption>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>Last run</TableHead>
                      <TableHead>Next run</TableHead>
                      <TableHead>Last success</TableHead>
                      <TableHead>Duration</TableHead>
                      <TableHead>Lock</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {jobs.data.jobs.map((job) => {
                      const state = deriveJobState(job);
                      return (
                        <TableRow key={job.name}>
                          <TableCell className="font-medium">{job.name}</TableCell>
                          <TableCell>
                            <JobStateBadges job={job} state={state} />
                          </TableCell>
                          <TableCell className="text-[var(--muted-foreground)]">
                            <LocalTime iso={job.lastRun} />
                          </TableCell>
                          <TableCell className="text-[var(--muted-foreground)]">
                            <LocalTime iso={job.nextRun} />
                          </TableCell>
                          <TableCell className="text-[var(--muted-foreground)]">
                            <LocalTime iso={job.lastSuccess} />
                          </TableCell>
                          <TableCell>
                            <DurationCell job={job} state={state} />
                          </TableCell>
                          <TableCell>
                            <LockCell lockState={job.lockState} />
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>

              {/* Mobile: stacked cards so nothing overflows at 320px. */}
              <ul className="space-y-3 md:hidden">
                {jobs.data.jobs.map((job) => {
                  const state = deriveJobState(job);
                  return (
                    <li
                      key={job.name}
                      className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-medium">{job.name}</span>
                        <JobStateBadges job={job} state={state} />
                      </div>
                      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                        <dt className="text-[var(--muted-foreground)]">Last run</dt>
                        <dd className="text-right">
                          <LocalTime iso={job.lastRun} />
                        </dd>
                        <dt className="text-[var(--muted-foreground)]">Next run</dt>
                        <dd className="text-right">
                          <LocalTime iso={job.nextRun} />
                        </dd>
                        <dt className="text-[var(--muted-foreground)]">Last success</dt>
                        <dd className="text-right">
                          <LocalTime iso={job.lastSuccess} />
                        </dd>
                        <dt className="text-[var(--muted-foreground)]">Duration</dt>
                        <dd className="text-right">
                          {state === 'running' ? formatDuration(job.currentDurationSeconds) : '-'}
                          <span className="ml-1 text-xs text-[var(--muted-foreground)]">
                            (exp {formatDuration(job.expectedDurationSeconds)})
                          </span>
                        </dd>
                        <dt className="text-[var(--muted-foreground)]">Lock</dt>
                        <dd className="flex justify-end">
                          <LockCell lockState={job.lockState} />
                        </dd>
                      </dl>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Errors summary (Req 5) - grouped buckets only, its own observability query.
//
// Renders ONLY what `ErrorsSummary` provides: grouped 4xx/5xx counts, by-source
// failure counts (API / job / storage / AI), a daily error-count trend, and the
// top failing route-classes (may be empty). It mounts NO raw log / stack /
// trace / exception / replay explorer - the dashboard is an operational summary,
// not a log explorer (Non-Goal, Req 21.2). Its own 7/30/90 window selector +
// loading/error/retry states, independent of the health tiles/jobs.
// ---------------------------------------------------------------------------

const ERROR_WINDOWS = [7, 30, 90] as const;

/** One grouped-count stat: a label + a formatted integer value. When the signal
 *  has no durable source it shows an explicit "Not instrumented" indicator
 *  instead of a misleading 0. */
function ErrorStat({
  label,
  value,
  notInstrumented,
}: {
  label: string;
  value: number;
  notInstrumented?: boolean;
}) {
  return (
    <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4">
      <p className="text-sm text-[var(--muted-foreground)]">{label}</p>
      {notInstrumented ? (
        <p
          className="mt-1 text-sm font-medium text-[var(--muted-foreground)]"
          title="No durable metric source - not instrumented"
        >
          Not instrumented
        </p>
      ) : (
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
      )}
    </div>
  );
}

/** The top failing route-classes list - grouped buckets, empty state when none. */
function TopRouteClasses({ data }: { data: ErrorsSummary }) {
  const rows = data.topRouteClasses ?? [];
  if (rows.length === 0) {
    // Distinguish "not tracked" from "zero failures": when the feature has no
    // durable per-route-class source, say so explicitly rather than implying
    // there were no failures.
    const notInstrumented = data.notInstrumented?.includes('topRouteClasses');
    return (
      <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
        {notInstrumented ? 'Not instrumented' : 'No failing route-classes recorded'}
      </p>
    );
  }
  return (
    <>
      {/* Desktop table. */}
      <div className="hidden md:block">
        <Table>
          <caption className="sr-only">
            Top failing route-classes by failure count over the selected window
          </caption>
          <TableHeader>
            <TableRow>
              <TableHead>Route class</TableHead>
              <TableHead>Failures</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.routeClass}>
                <TableCell className="font-medium">{r.routeClass}</TableCell>
                <TableCell className="tabular-nums">{r.failures.toLocaleString()}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile: stacked rows so nothing overflows at 320px. */}
      <ul className="space-y-3 md:hidden">
        {rows.map((r) => (
          <li
            key={r.routeClass}
            className="flex items-center justify-between gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-4"
          >
            <span className="font-medium">{r.routeClass}</span>
            <span className="tabular-nums">{r.failures.toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function ErrorsCard() {
  const [window, setWindow] = React.useState<MetricWindow>(30);
  const errors = useErrors(window);

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Errors</h2>
          {errors.data && (
            <span className="text-xs text-[var(--muted-foreground)]">
              As of <LocalTime iso={errors.data.computedAt} /> - last {errors.data.window} days
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="w-36">
            <Select
              value={String(window)}
              onValueChange={(value) => setWindow(Number(value) as MetricWindow)}
            >
              <SelectTrigger aria-label="Errors time window">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ERROR_WINDOWS.map((w) => (
                  <SelectItem key={w} value={String(w)}>
                    Last {w} days
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => errors.refetch()}
            disabled={errors.isFetching}
          >
            <RefreshCw className={`h-4 w-4 ${errors.isFetching ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>

      {/* aria-live so async results are announced without stealing focus. */}
      <div aria-live="polite" className="space-y-6">
        {errors.isError ? (
          <ErrorState
            title="Couldn't load errors summary"
            description={(errors.error as Error)?.message}
            onRetry={() => errors.refetch()}
          />
        ) : errors.isLoading || !errors.data ? (
          <LoadingSkeleton rows={3} />
        ) : (
          <>
            {/* Grouped 4xx / 5xx counts. */}
            <section aria-label="Grouped error counts">
              <div className="grid gap-4 sm:grid-cols-2">
                <ErrorStat label="Client errors (4xx)" value={errors.data.counts4xx} />
                <ErrorStat label="Server errors (5xx)" value={errors.data.counts5xx} />
              </div>
            </section>

            {/* Failure counts by originating subsystem (absent sources -> 0). */}
            <section aria-label="Errors by source">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                By source
              </h3>
              <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
                <ErrorStat label="API" value={errors.data.bySource.api} />
                <ErrorStat
                  label="Job"
                  value={errors.data.bySource.job}
                  notInstrumented={errors.data.notInstrumented?.includes('bySource.job')}
                />
                <ErrorStat
                  label="Storage"
                  value={errors.data.bySource.storage}
                  notInstrumented={errors.data.notInstrumented?.includes('bySource.storage')}
                />
                <ErrorStat label="AI" value={errors.data.bySource.ai} />
              </div>
            </section>

            {/* Daily error-count trend (SVG chart + a11y data-table fallback). */}
            <section aria-label="Daily error trend">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                Trend
              </h3>
              {errors.data.trend.length === 0 || errors.data.trend.every((p) => p.value === 0) ? (
                <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">
                  No errors recorded in this window.
                </p>
              ) : (
                <UsageChart
                  data={errors.data.trend}
                  label={`Total errors over the last ${errors.data.window} days`}
                  valueHeader="Errors"
                />
              )}
            </section>

            {/* Top failing route-classes (grouped buckets; may be empty). */}
            <section aria-label="Top failing route-classes">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                Top route-classes
              </h3>
              <TopRouteClasses data={errors.data} />
            </section>
          </>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Performance signals (Req 6) - its own observability query.
//
// Renders ONLY what `PerformanceSignals` provides - latency/cache aggregates the
// backend ALREADY produces. It mounts NO host-metric (CPU/RAM/disk) display: the
// backend omits those fields entirely (Non-Goal, Req 21.4), so they arrive as
// `undefined` and simply are not shown. Any field listed in `unavailable` (e.g.
// `dbQueryTimeMs`) is rendered with an explicit "Unavailable" indicator rather
// than a fabricated value (Req 6.7). No new instrumentation, no query params -
// its own loading/error/retry states, independent of the tiles/errors/jobs.
// ---------------------------------------------------------------------------

/** Format a millisecond aggregate (`1,234.5 ms`); `-` when absent. */
function formatMs(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return '-';
  return `${ms.toLocaleString(undefined, { maximumFractionDigits: 2 })} ms`;
}

/** A single performance stat: a value, or an explicit "Unavailable" badge. */
function PerfStat({
  label,
  value,
  unavailable,
}: {
  label: string;
  value: React.ReactNode;
  unavailable: boolean;
}) {
  return (
    <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4">
      <p className="text-sm text-[var(--muted-foreground)]">{label}</p>
      {unavailable ? (
        <p className="mt-1">
          <Badge variant="outline" aria-label={`${label}: unavailable`}>
            Unavailable
          </Badge>
        </p>
      ) : (
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      )}
    </div>
  );
}

/** Top slow route-classes table (avg + p95 when present); empty-state aware. */
function SlowRoutesTable({ rows }: { rows: RouteClassLatency[] }) {
  if (rows.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
        No route-class latency recorded
      </p>
    );
  }
  return (
    <>
      {/* Desktop table. */}
      <div className="hidden md:block">
        <Table>
          <caption className="sr-only">
            Slowest route-classes by average latency, with p95 where available
          </caption>
          <TableHeader>
            <TableRow>
              <TableHead>Route class</TableHead>
              <TableHead>Avg</TableHead>
              <TableHead>p95</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.routeClass}>
                <TableCell className="font-medium">{r.routeClass}</TableCell>
                <TableCell className="tabular-nums">{formatMs(r.avgMs)}</TableCell>
                <TableCell className="tabular-nums text-[var(--muted-foreground)]">
                  {r.p95Ms == null ? '-' : formatMs(r.p95Ms)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile: stacked rows so nothing overflows at 320px. */}
      <ul className="space-y-3 md:hidden">
        {rows.map((r) => (
          <li
            key={r.routeClass}
            className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4"
          >
            <p className="font-medium">{r.routeClass}</p>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-[var(--muted-foreground)]">Avg</dt>
              <dd className="text-right tabular-nums">{formatMs(r.avgMs)}</dd>
              <dt className="text-[var(--muted-foreground)]">p95</dt>
              <dd className="text-right tabular-nums">
                {r.p95Ms == null ? '-' : formatMs(r.p95Ms)}
              </dd>
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

/** Top slow background jobs table (avg duration); empty-state aware. */
function SlowJobsTable({ rows }: { rows: SlowJob[] }) {
  if (rows.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
        No background-job durations recorded
      </p>
    );
  }
  return (
    <>
      {/* Desktop table. */}
      <div className="hidden md:block">
        <Table>
          <caption className="sr-only">Slowest background jobs by average duration</caption>
          <TableHeader>
            <TableRow>
              <TableHead>Job</TableHead>
              <TableHead>Avg duration</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((j) => (
              <TableRow key={j.name}>
                <TableCell className="font-medium">{j.name}</TableCell>
                <TableCell className="tabular-nums">{formatMs(j.avgMs)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile: stacked rows. */}
      <ul className="space-y-3 md:hidden">
        {rows.map((j) => (
          <li
            key={j.name}
            className="flex items-center justify-between gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-4"
          >
            <span className="font-medium">{j.name}</span>
            <span className="tabular-nums">{formatMs(j.avgMs)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function PerformanceCard() {
  const performance = usePerformance();

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Performance</h2>
          {performance.data && (
            <span className="text-xs text-[var(--muted-foreground)]">
              As of <LocalTime iso={performance.data.computedAt} />
            </span>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => performance.refetch()}
          disabled={performance.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${performance.isFetching ? 'animate-spin' : ''}`} />{' '}
          Refresh
        </Button>
      </div>

      {/* aria-live so async results are announced without stealing focus. */}
      <div aria-live="polite" className="space-y-6">
        {performance.isError ? (
          <ErrorState
            title="Couldn't load performance signals"
            description={(performance.error as Error)?.message}
            onRetry={() => performance.refetch()}
          />
        ) : performance.isLoading || !performance.data ? (
          <LoadingSkeleton rows={3} />
        ) : (
          (() => {
            const data: PerformanceSignals = performance.data;
            // A field is "unavailable" when the backend lists it (Req 6.7) or its
            // value is absent - render an explicit indicator, never a fake value.
            const dbUnavailable =
              data.unavailable.includes('dbQueryTimeMs') || data.dbQueryTimeMs == null;
            const cacheUnavailable =
              data.unavailable.includes('cacheHitRatio') || data.cacheHitRatio == null;
            const cachePct =
              data.cacheHitRatio == null
                ? '-'
                : `${(data.cacheHitRatio * 100).toLocaleString(undefined, {
                    maximumFractionDigits: 1,
                  })}%`;
            return (
              <>
                {/* Headline aggregates: cache hit ratio + DB query time. */}
                <section aria-label="Performance summary">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <PerfStat
                      label="Cache hit ratio"
                      value={cachePct}
                      unavailable={cacheUnavailable}
                    />
                    <PerfStat
                      label="DB query time"
                      value={formatMs(data.dbQueryTimeMs)}
                      unavailable={dbUnavailable}
                    />
                  </div>
                </section>

                {/* Slowest route-classes by average latency (p95 where available). */}
                <section aria-label="Top slow route-classes">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                    Top slow route-classes
                  </h3>
                  <SlowRoutesTable rows={data.topSlowRoutes} />
                </section>

                {/* Slowest background jobs by average duration. */}
                <section aria-label="Top slow background jobs">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                    Top slow jobs
                  </h3>
                  <SlowJobsTable rows={data.topSlowJobs} />
                </section>
              </>
            );
          })()
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Configuration diagnostics (Req 10 / 11.4) - a strictly READ-ONLY tab.
//
// This section renders ONLY what `ConfigDiagnostics` provides and mounts NO
// input / edit / save / delete control of any kind (Req 10.3 / 11.4). Secrets
// never cross the boundary: each configured secret is shown solely as a
// "Configured / Not configured" presence indicator derived from a boolean
// (Req 10.4) - never a value.
// ---------------------------------------------------------------------------

/** On/off indicator for a boolean flag - text + color + icon (never color alone). */
function BoolIndicator({
  on,
  onLabel = 'On',
  offLabel = 'Off',
}: {
  on: boolean;
  onLabel?: string;
  offLabel?: string;
}) {
  return on ? (
    <Badge variant="success" aria-label={onLabel}>
      <CheckCircle className="h-3.5 w-3.5" aria-hidden />
      {onLabel}
    </Badge>
  ) : (
    <Badge variant="neutral" aria-label={offLabel}>
      <Circle className="h-3.5 w-3.5" aria-hidden />
      {offLabel}
    </Badge>
  );
}

/** A labelled list of boolean entries (feature flags / kill switches). */
function BoolList({
  entries,
  emptyLabel,
}: {
  entries: Record<string, boolean>;
  emptyLabel: string;
}) {
  const keys = Object.keys(entries).sort();
  if (keys.length === 0) {
    return <p className="text-sm text-[var(--muted-foreground)]">{emptyLabel}</p>;
  }
  return (
    <ul className="divide-y divide-[var(--border)] text-sm">
      {keys.map((key) => (
        <li key={key} className="flex items-center justify-between gap-4 py-1.5">
          <span className="font-mono text-xs text-[var(--foreground)]">{key}</span>
          <BoolIndicator on={entries[key]} />
        </li>
      ))}
    </ul>
  );
}

/** One read-only config field (definition-list row). */
function ConfigField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <dt className="text-[var(--muted-foreground)]">{label}</dt>
      <dd className="text-right font-medium">{value ?? '-'}</dd>
    </div>
  );
}

function ConfigTab() {
  const config = useConfig();

  if (config.isError) {
    // Explicit error state with a working retry control (Req 11.15).
    return (
      <ErrorState
        title="Couldn't load configuration"
        description={(config.error as Error)?.message}
        onRetry={() => config.refetch()}
      />
    );
  }
  if (config.isLoading || !config.data) {
    // Explicit loading state (Req 11.15).
    return <LoadingSkeleton rows={3} />;
  }

  const data: ConfigDiagnostics = config.data;
  const configuredKeys = Object.keys(data.configured).sort();
  const versionKeys = Object.keys(data.versions).sort();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
          As of <LocalTime iso={data.computedAt} />
          <Badge variant="outline" aria-label="This tab is read-only">
            Read-only
          </Badge>
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => config.refetch()}
          disabled={config.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${config.isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Environment + providers + scheduler settings. */}
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">Environment</h2>
          <dl className="divide-y divide-[var(--border)] text-sm">
            <ConfigField label="Environment" value={data.env} />
            <ConfigField label="Scheduler mode" value={data.schedulerMode} />
            <ConfigField label="Grace period (days)" value={data.gracePeriodDays} />
            <ConfigField label="Storage provider" value={data.storageProvider} />
            <ConfigField label="Email provider" value={data.emailProvider} />
            <ConfigField
              label="Active AI providers"
              value={data.activeAiProviders.length ? data.activeAiProviders.join(', ') : 'None'}
            />
            <div className="flex items-center justify-between gap-4 py-1.5">
              <dt className="text-[var(--muted-foreground)]">Maintenance mode</dt>
              <dd>
                <BoolIndicator on={data.maintenanceMode} onLabel="Enabled" offLabel="Disabled" />
              </dd>
            </div>
          </dl>
        </Card>

        {/* Configured-secret presence booleans - NEVER a value (Req 10.4). */}
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">
            Configured secrets
          </h2>
          {configuredKeys.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">Nothing reported.</p>
          ) : (
            <ul className="divide-y divide-[var(--border)] text-sm">
              {configuredKeys.map((key) => (
                <li key={key} className="flex items-center justify-between gap-4 py-1.5">
                  <span className="font-mono text-xs text-[var(--foreground)]">{key}</span>
                  <BoolIndicator
                    on={data.configured[key]}
                    onLabel="Configured"
                    offLabel="Not configured"
                  />
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Feature flags. */}
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">
            Feature flags
          </h2>
          <BoolList entries={data.featureFlags} emptyLabel="No feature flags reported." />
        </Card>

        {/* Kill switches. */}
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">
            Kill switches
          </h2>
          <BoolList entries={data.killSwitches} emptyLabel="No kill switches reported." />
        </Card>

        {/* Version identifiers. */}
        <Card className="p-5 lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">Versions</h2>
          {versionKeys.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">No versions reported.</p>
          ) : (
            <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
              {versionKeys.map((key) => (
                <div key={key} className="flex items-center justify-between gap-4 py-1.5">
                  <dt className="text-[var(--muted-foreground)]">{key}</dt>
                  <dd className="font-mono text-xs">{data.versions[key]}</dd>
                </div>
              ))}
            </dl>
          )}
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Maintenance panel (Req 11.6 / 18) - the ONLY manage-controls on this page.
//
// Rendered ONLY to manage-capable admins. Capability gating note: the client
// session currently exposes a single `isAdmin` flag (role === 'admin') and does
// NOT model the finer admin.read-vs-admin.manage distinction. We therefore gate
// on `isAdmin`; a hypothetical read-only admin isn't represented client-side.
// This is a UX affordance only - the backend still enforces `require_admin_manage`
// on every action, so hiding the panel is never the security boundary.
// ---------------------------------------------------------------------------

const MAINTENANCE_ACTIONS: { action: MaintenanceAction; label: string; description: string }[] = [
  {
    action: 'refresh-metrics',
    label: 'Refresh metrics',
    description: 'Refresh cached metric snapshots',
  },
  { action: 'run-rollup', label: 'Run rollup', description: 'Re-invoke the metrics rollup job' },
  { action: 'run-cleanup', label: 'Run cleanup', description: 'Re-invoke the cleanup / purge job' },
  {
    action: 'run-retention',
    label: 'Run retention',
    description: 'Re-invoke the audit-retention job',
  },
];

/** Map a maintenance dispatch outcome to human copy + a toast variant. */
function describeOutcome(
  label: string,
  status: 'started' | 'already_running' | 'disabled'
): { message: string; variant: 'success' | 'info' } {
  switch (status) {
    case 'started':
      return { message: `${label} started.`, variant: 'success' };
    case 'already_running':
      return { message: `${label} is already running.`, variant: 'info' };
    case 'disabled':
      return { message: `${label} is disabled in this environment.`, variant: 'info' };
  }
}

function MaintenancePanel() {
  const { isAdmin } = useSession();
  const { toast } = useToast();
  const runMaintenance = useRunMaintenance();
  const [status, setStatus] = React.useState<string>('');

  // Req 11.6: hide every admin.manage control from principals without manage.
  if (!isAdmin) return null;

  const pendingAction = runMaintenance.isPending
    ? (runMaintenance.variables as MaintenanceAction | undefined)
    : undefined;

  function trigger(action: MaintenanceAction, label: string) {
    setStatus(`Starting ${label}...`);
    runMaintenance.mutate(action, {
      onSuccess: (result) => {
        const { message, variant } = describeOutcome(label, result.status);
        setStatus(message);
        toast({ title: message, variant });
      },
      onError: (error) => {
        const message = `${label} failed: ${(error as Error)?.message ?? 'Unknown error'}`;
        setStatus(message);
        toast({ title: message, variant: 'error' });
      },
    });
  }

  return (
    <Card className="p-5">
      <div className="mb-1 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Maintenance</h2>
        <Badge variant="ai" aria-label="Requires manage capability">
          Manage
        </Badge>
      </div>
      <p className="mb-4 text-sm text-[var(--muted-foreground)]">
        Re-invoke a background job. Each action is safe and idempotent - a job that is already
        running is left untouched.
      </p>

      <div className="flex flex-wrap gap-2">
        {MAINTENANCE_ACTIONS.map(({ action, label, description }) => {
          const isThisPending = pendingAction === action;
          return (
            <Button
              key={action}
              variant="outline"
              size="sm"
              title={description}
              onClick={() => trigger(action, label)}
              // Disable ALL buttons while any action is in flight (single-flight UX).
              disabled={runMaintenance.isPending}
            >
              {isThisPending ? (
                <Loader className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden />
              )}
              {label}
            </Button>
          );
        })}
      </div>

      {/* Async results announced through an aria-live region (Req 11.12). */}
      <p
        className="mt-3 min-h-[1.25rem] text-sm text-[var(--muted-foreground)]"
        role="status"
        aria-live="polite"
      >
        {status}
      </p>
    </Card>
  );
}
