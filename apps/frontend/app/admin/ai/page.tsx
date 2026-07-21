'use client';

/**
 * Admin AI Analytics (Task 9.4 / Req 4).
 *
 * Surfaces the allowlisted AI-call aggregates the backend `AiMetricsService`
 * computes: headline metric cards (total calls, success/failure rate, average
 * latency, average tokens/call, timeouts, retries, estimated cost) plus a
 * per-provider breakdown table and an optional daily-calls trend. A window
 * selector (7 / 30 / 90 days, default 30) refetches the series when changed
 * (Req 4.3); the backend validates the 1-365 range server-side.
 *
 * This page renders ONLY what `AiAnalytics` provides - aggregate counts, rates
 * and a truncated whole-dollar cost estimate. It deliberately shows no prompt,
 * model, temperature or per-call/id fields (allowlist - Req 4). On fetch failure
 * it shows an explicit error state with a working retry control (never a blank
 * or partial view); rates degrade sensibly to 0% / - when there are no calls.
 */
import * as React from 'react';
import { Suspense } from 'react';
import { useSearchParams, useRouter, usePathname } from 'next/navigation';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import { Card } from '@/components/atelier/card';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import { UsageChart } from '@/components/admin/mini-chart';
import { LocalTime } from '@/components/admin/local-time';
import { useAiAnalytics } from '@/features/admin/hooks';
import type { AiAnalytics } from '@/lib/api/admin';

const WINDOWS = [7, 30, 90] as const;
type AiWindow = (typeof WINDOWS)[number];

// ---------------------------------------------------------------------------
// Formatters - pure + null-safe so a zero-calls window renders sensibly.
// ---------------------------------------------------------------------------

/** A 0.0-1.0 fraction as a percentage string ("98.00%"); "-" when no calls. */
function formatRate(fraction: number, totalCalls: number): string {
  if (totalCalls <= 0) return '-';
  return `${(fraction * 100).toFixed(2)}%`;
}

/** A millisecond value ("142.5 ms"); "-" when there is nothing to average. */
function formatMs(ms: number, totalCalls: number): string {
  if (totalCalls <= 0) return '-';
  return `${ms.toLocaleString(undefined, { maximumFractionDigits: 2 })} ms`;
}

/** Average tokens per call; "-" when there is nothing to average. */
function formatAvg(value: number, totalCalls: number): string {
  if (totalCalls <= 0) return '-';
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Whole-dollar cost estimate ("$12"). */
function formatDollars(dollars: number): string {
  return `$${Math.max(0, Math.trunc(dollars)).toLocaleString()}`;
}

/** One headline metric card: a label + a formatted value (+ optional sub-text). */
function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <Card className="p-5">
      <p className="text-sm text-[var(--muted-foreground)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-[var(--muted-foreground)]">{hint}</p>}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Provider breakdown table (Req 4) - a11y caption + responsive stacked mobile.
// ---------------------------------------------------------------------------

function ProviderTable({ data }: { data: AiAnalytics }) {
  const providers = data.providers ?? [];
  const totalCalls = providers.reduce((sum, p) => sum + p.calls, 0);
  const pct = (calls: number) =>
    totalCalls > 0 ? `${((calls / totalCalls) * 100).toFixed(1)}%` : '-';

  return (
    <Card className="p-5">
      <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">
        Provider breakdown
      </h2>

      {providers.length === 0 ? (
        <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
          No provider activity reported.
        </p>
      ) : (
        <>
          {/* Desktop table. */}
          <div className="hidden md:block">
            <Table>
              <caption className="sr-only">AI calls by provider over the selected window</caption>
              <TableHeader>
                <TableRow>
                  <TableHead>Provider</TableHead>
                  <TableHead>Calls</TableHead>
                  <TableHead>Share</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {providers.map((p) => (
                  <TableRow key={p.provider}>
                    <TableCell className="font-medium">{p.provider}</TableCell>
                    <TableCell className="tabular-nums">{p.calls.toLocaleString()}</TableCell>
                    <TableCell className="tabular-nums text-[var(--muted-foreground)]">
                      {pct(p.calls)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Mobile: stacked cards so nothing overflows at 320px. */}
          <ul className="space-y-3 md:hidden">
            {providers.map((p) => (
              <li
                key={p.provider}
                className="flex items-center justify-between gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-4"
              >
                <span className="font-medium">{p.provider}</span>
                <span className="text-right">
                  <span className="tabular-nums">{p.calls.toLocaleString()}</span>
                  <span className="ml-2 text-xs text-[var(--muted-foreground)]">
                    {pct(p.calls)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminAiPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={3} />}>
      <AdminAiPageInner />
    </Suspense>
  );
}

function AdminAiPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const windowParam = Number(params.get('window'));
  const window: AiWindow = (WINDOWS as readonly number[]).includes(windowParam)
    ? (windowParam as AiWindow)
    : 30;

  const setWindow = (value: string) => {
    const sp = new URLSearchParams(params.toString());
    sp.set('window', value);
    router.replace(`${pathname}?${sp.toString()}`);
  };

  const { data, isLoading, isError, error, isFetching, refetch } = useAiAnalytics(window);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">AI Analytics</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            AI provider usage, reliability and estimated cost over the selected window.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-40">
            <Select value={String(window)} onValueChange={setWindow}>
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
          <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>

      {/* aria-live so async results are announced without stealing focus. */}
      <div aria-live="polite" className="space-y-6">
        {isError ? (
          <ErrorState
            title="Couldn't load AI analytics"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading || !data ? (
          <LoadingSkeleton rows={3} />
        ) : (
          <>
            <p className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
              As of <LocalTime iso={data.computedAt} />
              <span>- last {data.window} days</span>
            </p>

            {/* Headline metric cards. */}
            <section aria-label="AI headline metrics">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard label="Total calls" value={data.totalCalls.toLocaleString()} />
                <MetricCard
                  label="Success rate"
                  value={formatRate(data.successRate, data.totalCalls)}
                />
                <MetricCard
                  label="Failure rate"
                  value={formatRate(data.failureRate, data.totalCalls)}
                />
                <MetricCard
                  label="Avg latency"
                  value={formatMs(data.avgLatencyMs, data.totalCalls)}
                />
                <MetricCard
                  label="Avg tokens / call"
                  value={formatAvg(data.avgUnitsPerCall, data.totalCalls)}
                />
                <MetricCard label="Timeouts" value={data.timeouts.toLocaleString()} />
                <MetricCard label="Retries" value={data.retries.toLocaleString()} />
                <MetricCard
                  label="Estimated cost"
                  value={formatDollars(data.estimatedCostDollars)}
                  hint="Truncated whole dollars"
                />
              </div>
            </section>

            {/* Provider breakdown table. */}
            <ProviderTable data={data} />

            {/* Optional daily AI-calls trend (SVG chart + a11y data-table fallback). */}
            {data.daily && data.daily.length > 0 && (
              <Card className="p-5">
                <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">
                  Daily AI calls
                </h2>
                {data.daily.every((p) => p.value === 0) ? (
                  <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">
                    No AI calls in this window yet.
                  </p>
                ) : (
                  <UsageChart
                    data={data.daily}
                    label={`AI calls over the last ${data.window} days`}
                    valueHeader="AI calls"
                  />
                )}
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}
