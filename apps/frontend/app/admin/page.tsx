'use client';

/**
 * Admin Overview (Task 14.3 / Req 11, 13).
 *
 * The Overview page is the operational landing screen. It now folds in the
 * former standalone Analytics page (Req 11.1): a single windowed usage chart
 * with a 7/30/90-day selector (default 30) lives alongside the headline KPI
 * cards, so there is one place to see "is the platform being used".
 *
 * KPI cards (Req 13) read `GET /admin/kpis` via {@link useKpis}. Each card is a
 * labeled value (Req 13.8); a KPI the backend reports as `unavailable` (or a
 * null value) renders an explicit "Unavailable" indicator instead of a bogus
 * number (Req 13.10); `errorRate24h` renders as a percentage ("1.25%"). While
 * the snapshot's age exceeds 60 seconds - or the backend `stale` flag is set -
 * every card shows a "Stale" badge and the page offers a working refresh control
 * that re-fetches and clears the indicator on success (Req 13.9 / 11.9 / 11.10).
 *
 * The usage chart uses {@link useUsageSeries} keyed by the selected metric +
 * window, so changing either re-renders within 2s (Req 11.2). The chart keeps
 * its title + visually-hidden data-table fallback (Req 11.13) and its own
 * loading/error/retry states (Req 11.15). Async results are announced via an
 * `aria-live` region (Req 11.12) and every control is keyboard-operable.
 *
 * NOTE: the Product-usage section (feature usage + resume analytics) is added to
 * this page by tasks 16.3 / 17.3 - this page is structured to leave room for it
 * but does not stub it here.
 */
import * as React from 'react';
import Users from 'lucide-react/dist/esm/icons/users';
import UserPlus from 'lucide-react/dist/esm/icons/user-plus';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import { UsageChart } from '@/components/admin/mini-chart';
import { LocalTime } from '@/components/admin/local-time';
import {
  useFeatureUsage,
  useKpis,
  useResumeAnalytics,
  useUsageSeries,
} from '@/features/admin/hooks';
import type { KpiValue, MetricName, MetricWindow } from '@/lib/api/admin';

// ---------------------------------------------------------------------------
// Windowed-chart config - the metric + window options folded in from the former
// Analytics page. Default window is 30 days (Req 11.1).
// ---------------------------------------------------------------------------

const WINDOWS: MetricWindow[] = [7, 30, 90];

const METRICS: { value: MetricName; label: string }[] = [
  { value: 'signups', label: 'Sign-ups' },
  { value: 'active_users', label: 'Active users' },
  { value: 'resumes_tailored', label: 'Resumes tailored' },
];

// ---------------------------------------------------------------------------
// KPI formatting - count KPIs are non-negative integers; the error-rate KPI is a
// percentage bounded 0.00-100.00 rendered with two decimals ("1.25%").
// ---------------------------------------------------------------------------

function formatCount(value: number): string {
  return Math.max(0, Math.round(value)).toLocaleString();
}

function formatPercent(value: number): string {
  return `${value.toFixed(2)}%`;
}

// ---------------------------------------------------------------------------
// Presentational pieces
// ---------------------------------------------------------------------------

/**
 * One KPI card: a labeled value (Req 13.8). When the KPI is `unavailable` (or
 * its value is null) it renders an explicit "Unavailable" indicator in place of
 * the number (Req 13.10). While the snapshot is stale it shows a "Stale" badge
 * (text + colour, never colour alone - a11y) on the card (Req 13.9).
 */
function KpiCard({
  icon: Icon,
  label,
  kpi,
  format = formatCount,
  stale,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  kpi: KpiValue;
  format?: (value: number) => string;
  stale: boolean;
}) {
  const unavailable = kpi.unavailable || kpi.value == null;
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 text-[var(--muted-foreground)]">
          <Icon className="h-4 w-4" aria-hidden />
          <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
        </div>
        {stale && (
          <Badge variant="warning" aria-label={`${label} may be stale`}>
            Stale
          </Badge>
        )}
      </div>
      <p className="mt-2 text-3xl font-semibold tabular-nums">
        {unavailable ? (
          <Badge variant="outline" aria-label={`${label}: unavailable`}>
            Unavailable
          </Badge>
        ) : (
          format(kpi.value as number)
        )}
      </p>
    </Card>
  );
}

/** The windowed usage chart folded in from the former Analytics page. */
function UsageSection() {
  const [metric, setMetric] = React.useState<MetricName>('signups');
  const [window, setWindowState] = React.useState<MetricWindow>(30);
  const { data, isLoading, isError, error, isFetching, refetch } = useUsageSeries(metric, window);

  const metricLabel = METRICS.find((m) => m.value === metric)?.label ?? 'Usage';

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">
          {metricLabel} (last {window} days)
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-44">
            <Select value={metric} onValueChange={(v) => setMetric(v as MetricName)}>
              <SelectTrigger aria-label="Metric">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {METRICS.map((m) => (
                  <SelectItem key={m.value} value={m.value}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-36">
            <Select
              value={String(window)}
              onValueChange={(v) => setWindowState(Number(v) as MetricWindow)}
            >
              <SelectTrigger aria-label="Time window">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WINDOWS.map((w) => (
                  <SelectItem key={w} value={String(w)}>
                    Last {w} days
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* aria-live so async chart results are announced without stealing focus. */}
      <div aria-live="polite" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            title="Chart unavailable"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading || !data ? (
          <LoadingSkeleton rows={1} />
        ) : data.points.every((p) => p.value === 0) ? (
          <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">
            No {metricLabel.toLowerCase()} in this window yet.
          </p>
        ) : (
          <UsageChart
            data={data.points}
            label={`${metricLabel} over the last ${window} days`}
            valueHeader={metricLabel}
          />
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Product Usage - feature usage analytics (Req 16, 19.1)
//
// This is a DISTINCT bounded context from observability. It renders below the
// operational KPIs/chart as a visually-separate section so admins can see product
// adoption metrics alongside (but not confused with) operational health data.
// ---------------------------------------------------------------------------

/**
 * Friendly, human-readable names for the eight tracked product features. The
 * backend emits raw feature keys (optionally `feat_`-prefixed); this map turns
 * them into labels an admin can read. Unknown keys fall back to the raw value.
 */
const FEATURE_LABELS: Record<string, string> = {
  builder: 'Resume builder',
  tailor: 'Resume tailor',
  parser: 'Resume parser',
  import: 'Resume import',
  cover_letter: 'Cover letter',
  profile_gen: 'Profile generator',
  portfolio: 'Portfolio',
  jd_parse: 'Job description parse',
};

function featureLabel(feature: string): string {
  const key = feature.startsWith('feat_') ? feature.slice('feat_'.length) : feature;
  return FEATURE_LABELS[key] ?? feature;
}

// ---------------------------------------------------------------------------
// Resume analytics (Req 14, 19.1) - the second product-analytics panel, living
// in the SAME "Product usage" section as feature usage. Source split (where
// resumes come from), the most-used templates, and a compact growth summary.
// ---------------------------------------------------------------------------

/** The four resume origins, in display order, with human-readable labels. */
const RESUME_SOURCES: { key: 'generated' | 'imported' | 'tailored' | 'deleted'; label: string }[] =
  [
    { key: 'generated', label: 'Generated' },
    { key: 'imported', label: 'Imported' },
    { key: 'tailored', label: 'Tailored' },
    { key: 'deleted', label: 'Deleted' },
  ];

/**
 * Resume analytics panel (Req 14): a source split (count + % for each origin),
 * the most-used templates, and a growth total over the selected window. Reuses
 * the section primitives (Card / Select / LoadingSkeleton / ErrorState) and has
 * its own window selector + `aria-live` region so it loads/errors/refreshes
 * independently of the feature-usage table above it.
 */
function ResumeAnalyticsPanel() {
  const [window, setWindowState] = React.useState<MetricWindow>(30);
  const { data, isLoading, isError, error, isFetching, refetch } = useResumeAnalytics(window);

  const growthTotal = data ? data.growth.reduce((sum, p) => sum + p.value, 0) : 0;

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">Resume analytics</h3>
          <p className="text-sm text-[var(--muted-foreground)]">
            Where resumes come from and which templates lead.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-36">
            <Select
              value={String(window)}
              onValueChange={(v) => setWindowState(Number(v) as MetricWindow)}
            >
              <SelectTrigger aria-label="Resume analytics time window">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WINDOWS.map((w) => (
                  <SelectItem key={w} value={String(w)}>
                    Last {w} days
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      <div aria-live="polite" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            title="Resume analytics unavailable"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading || !data ? (
          <LoadingSkeleton rows={2} />
        ) : (
          <div className="space-y-6">
            {/* Source split - counts + percentages for each origin. */}
            <div>
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Source split
              </h4>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {RESUME_SOURCES.map((s) => {
                  const count = data.sourceSplit[s.key];
                  const pct = data.sourceSplit[`${s.key}Pct` as const];
                  return (
                    <div key={s.key} className="rounded-md border border-[var(--border)] p-3">
                      <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                        {s.label}
                      </p>
                      <p className="mt-1 text-2xl font-semibold tabular-nums">
                        {count.toLocaleString()}
                      </p>
                      <p className="text-xs text-[var(--muted-foreground)] tabular-nums">
                        {pct.toFixed(1)}%
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Popular templates - name + usage count, or an empty state. */}
            <div>
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Popular templates
              </h4>
              {data.topTemplates.length === 0 ? (
                <p className="py-4 text-center text-sm text-[var(--muted-foreground)]">
                  No template usage recorded in this window yet.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table
                    className="w-full text-sm"
                    aria-label={`Popular templates over the last ${window} days`}
                  >
                    <thead>
                      <tr className="border-b text-left text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                        <th className="pb-2 pr-4">Template</th>
                        <th className="pb-2 text-right">Uses</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.topTemplates.map((t) => (
                        <tr key={t.name} className="border-b last:border-0">
                          <td className="py-2 pr-4 font-medium">{t.name}</td>
                          <td className="py-2 text-right tabular-nums">
                            {t.count.toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Compact growth summary - total new resumes over the window. */}
            <p className="text-sm text-[var(--muted-foreground)]">
              <span className="font-semibold tabular-nums text-[var(--foreground)]">
                {growthTotal.toLocaleString()}
              </span>{' '}
              resumes created in the last {window} days.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}

/** Product-usage section: feature invocation totals over a selectable window. */
function ProductUsageSection() {
  const [window, setWindowState] = React.useState<MetricWindow>(30);
  const { data, isLoading, isError, error, isFetching, refetch } = useFeatureUsage(window);

  return (
    <section aria-label="Product usage" className="space-y-4 border-t border-[var(--border)] pt-6">
      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Product usage</h2>
            <p className="text-sm text-[var(--muted-foreground)]">
              Feature adoption across the product - separate from platform health.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-36">
              <Select
                value={String(window)}
                onValueChange={(v) => setWindowState(Number(v) as MetricWindow)}
              >
                <SelectTrigger aria-label="Feature usage time window">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WINDOWS.map((w) => (
                    <SelectItem key={w} value={String(w)}>
                      Last {w} days
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <div aria-live="polite" aria-busy={isFetching}>
          {isError ? (
            <ErrorState
              title="Feature usage unavailable"
              description={(error as Error)?.message}
              onRetry={() => refetch()}
            />
          ) : isLoading || !data ? (
            <LoadingSkeleton rows={2} />
          ) : data.series.length === 0 ? (
            <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">
              No feature usage recorded in this window yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table
                className="w-full text-sm"
                aria-label={`Feature usage over the last ${window} days`}
              >
                <thead>
                  <tr className="border-b text-left text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                    <th className="pb-2 pr-4">Feature</th>
                    <th className="pb-2 text-right">Total invocations</th>
                  </tr>
                </thead>
                <tbody>
                  {data.series.map((s) => (
                    <tr key={s.feature} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-medium">{featureLabel(s.feature)}</td>
                      <td className="py-2 text-right tabular-nums">{s.total.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      {/* Resume analytics - second product-analytics panel in the same section. */}
      <ResumeAnalyticsPanel />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminOverviewPage() {
  const { data, isLoading, isError, error, isFetching, refetch } = useKpis();

  // Staleness (Req 13.9): the snapshot is stale when the backend says so OR when
  // its computed age exceeds 60 seconds. A lightweight ticking clock re-evaluates
  // the age so an idle page flips to "Stale" once it crosses the threshold.
  const [nowMs, setNowMs] = React.useState(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 15_000);
    return () => clearInterval(id);
  }, []);
  const ageSeconds = data ? (nowMs - new Date(data.computedAt).getTime()) / 1000 : 0;
  const isStale = !!data && (data.stale || ageSeconds > 60);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Overview</h1>
          <p className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
            {data ? (
              <>
                As of <LocalTime iso={data.computedAt} />
                {isStale && (
                  <Badge variant="warning" aria-label="Data may be stale">
                    Stale
                  </Badge>
                )}
              </>
            ) : (
              'Platform metrics and usage at a glance.'
            )}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {/* KPI cards - aria-live so async results are announced (Req 11.12). */}
      <div aria-live="polite" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            title="Couldn't load KPIs"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading || !data ? (
          <LoadingSkeleton rows={2} />
        ) : (
          <section aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <KpiCard icon={Users} label="Total users" kpi={data.totalUsers} stale={isStale} />
            <KpiCard
              icon={UserPlus}
              label="New users today"
              kpi={data.newUsersToday}
              stale={isStale}
            />
            <KpiCard
              icon={Sparkles}
              label="AI calls today"
              kpi={data.aiCallsToday}
              stale={isStale}
            />
            <KpiCard
              icon={TriangleAlert}
              label="Error rate (24h)"
              kpi={data.errorRate24h}
              format={formatPercent}
              stale={isStale}
            />
            <KpiCard icon={Trash2} label="Purge backlog" kpi={data.purgeBacklog} stale={isStale} />
          </section>
        )}
      </div>

      {/* Windowed usage chart (folded in from the former Analytics page). */}
      <UsageSection />

      {/* Product usage - distinct bounded context from observability (Req 19.1). */}
      <ProductUsageSection />
    </div>
  );
}
