import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

/**
 * Admin Overview page (Task 14.4 / Req 11.1, 13.7, 13.8, 13.9, 13.10).
 *
 * Component-level render + interaction-contract checks with the `useKpis` and
 * `useUsageSeries` hooks mocked:
 *
 * - **KPI cards (Req 13.8/13.10).** Each KPI renders as a labeled card; a KPI the
 *   backend reports as `unavailable` shows an explicit "Unavailable" indicator
 *   instead of a number; `errorRate24h` renders as a percentage ("2.50%").
 * - **Stale indicator (Req 13.9).** When the snapshot's `computedAt` is older
 *   than 60s — or the backend `stale` flag is set — a "Stale" indicator renders.
 * - **Selector re-render (Req 11.1/11.2).** The usage chart is keyed by the
 *   selected metric + window via `useUsageSeries`, so a metric/window change
 *   re-queries. The initial render calls `useUsageSeries` with the default
 *   metric + 30-day window, and both the metric and time-window selectors render
 *   labeled. The atelier `Select` is a Radix primitive that is not reliably
 *   operable under jsdom (it portals + relies on pointer-capture APIs jsdom
 *   lacks), so the full click-through interaction (selector change → chart
 *   re-query within 2s) is owned by the Playwright E2E in task 19.1; this test
 *   pins the render/wiring contract at the unit level.
 */

import type { OverviewKpis, UsageSeries } from '@/lib/api/admin';

const useKpisMock = vi.fn();
const useUsageSeriesMock = vi.fn();
// The Overview page also renders the "Product usage" section (feature usage +
// resume analytics), each backed by its own product-analytics hook. This test
// drives the KPI + usage-chart contract; the product-analytics hooks are stubbed
// to a benign loading state so that section renders its skeletons without
// affecting the KPI/stale/selector assertions below.
const _idleQuery = () => ({
  data: undefined,
  isError: false,
  isLoading: true,
  isFetching: false,
  error: null,
  refetch: vi.fn(),
});
vi.mock('@/features/admin/hooks', () => ({
  useKpis: () => useKpisMock(),
  useUsageSeries: (...args: unknown[]) => useUsageSeriesMock(...args),
  useFeatureUsage: () => _idleQuery(),
  useResumeAnalytics: () => _idleQuery(),
}));

import AdminOverviewPage from '@/app/admin/page';

// A healthy KPI snapshot with ONE unavailable card (purgeBacklog) to exercise
// the "Unavailable" indicator path (Req 13.10).
function kpiSnapshot(over: Partial<OverviewKpis> = {}): OverviewKpis {
  return {
    totalUsers: { value: 1500, unavailable: false },
    newUsersToday: { value: 12, unavailable: false },
    aiCallsToday: { value: 42, unavailable: false },
    errorRate24h: { value: 2.5, unavailable: false },
    purgeBacklog: { value: null, unavailable: true },
    computedAt: new Date().toISOString(),
    stale: false,
    ...over,
  };
}

const USAGE: UsageSeries = {
  metric: 'signups',
  window: 30,
  points: [
    { date: '2026-01-01', value: 3 },
    { date: '2026-01-02', value: 5 },
  ],
  computedAt: '2026-01-02T00:00:00+00:00',
};

function kpisQuery(over: Record<string, unknown> = {}) {
  return {
    data: kpiSnapshot(),
    isError: false,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  };
}

function usageQuery(over: Record<string, unknown> = {}) {
  return {
    data: USAGE,
    isError: false,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

describe('AdminOverviewPage — KPI cards', () => {
  it('renders every labeled KPI card with its value', () => {
    useKpisMock.mockReturnValue(kpisQuery());
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    for (const label of [
      'Total users',
      'New users today',
      'AI calls today',
      'Error rate (24h)',
      'Purge backlog',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // Count KPI rendered with locale formatting.
    expect(screen.getByText('1,500')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('shows an "Unavailable" indicator (not a number) for an unavailable KPI', () => {
    useKpisMock.mockReturnValue(kpisQuery());
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    // purgeBacklog is unavailable → explicit indicator, never a bogus 0 (Req 13.10).
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.getByLabelText('Purge backlog: unavailable')).toBeInTheDocument();
  });

  it('renders errorRate24h as a percentage', () => {
    useKpisMock.mockReturnValue(kpisQuery());
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    // 2.5 → "2.50%" (two decimals, Req 13.8).
    expect(screen.getByText('2.50%')).toBeInTheDocument();
  });
});

describe('AdminOverviewPage — stale indicator (Req 13.9)', () => {
  it('renders a "Stale" indicator when computedAt is older than 60s', () => {
    const sixMinutesAgo = new Date(Date.now() - 6 * 60_000).toISOString();
    useKpisMock.mockReturnValue(kpisQuery({ data: kpiSnapshot({ computedAt: sixMinutesAgo }) }));
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    expect(screen.getAllByText('Stale').length).toBeGreaterThanOrEqual(1);
  });

  it('renders a "Stale" indicator when the backend stale flag is set', () => {
    useKpisMock.mockReturnValue(kpisQuery({ data: kpiSnapshot({ stale: true }) }));
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    expect(screen.getAllByText('Stale').length).toBeGreaterThanOrEqual(1);
  });

  it('does NOT show a "Stale" indicator for a fresh, non-stale snapshot', () => {
    useKpisMock.mockReturnValue(kpisQuery()); // computedAt = now, stale = false
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    expect(screen.queryByText('Stale')).not.toBeInTheDocument();
  });
});

describe('AdminOverviewPage — usage chart selectors (Req 11.1)', () => {
  it('queries useUsageSeries with the default metric + 30-day window on mount', () => {
    useKpisMock.mockReturnValue(kpisQuery());
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    // The chart re-queries via this hook keyed by (metric, window). Default is
    // the first metric ('signups') over a 30-day window (Req 11.1).
    expect(useUsageSeriesMock).toHaveBeenCalledWith('signups', 30);
  });

  it('renders both labeled selectors so the window/metric can be changed', () => {
    useKpisMock.mockReturnValue(kpisQuery());
    useUsageSeriesMock.mockReturnValue(usageQuery());
    render(<AdminOverviewPage />);

    // Both selects are present + labeled (keyboard/AT operable). Changing them
    // re-invokes useUsageSeries with new args — the click-through is covered by
    // the E2E in task 19.1 (Radix Select is not reliably driveable in jsdom).
    expect(screen.getByLabelText('Metric')).toBeInTheDocument();
    expect(screen.getByLabelText('Time window')).toBeInTheDocument();
    // Default window label rendered on the trigger. The Overview now hosts
    // several independent 30-day windows (usage chart + the Product-usage
    // feature-usage/resume-analytics panels), so at least one is present.
    expect(screen.getAllByText('Last 30 days').length).toBeGreaterThanOrEqual(1);
  });
});
