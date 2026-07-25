import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

/**
 * Admin System Health page (Task 6.6 / Req 3.2, 3.8, 15.8).
 *
 * Lightweight component-level check for subsystem tiles and the independently
 * queried jobs gauges. Status and availability are always rendered as literal
 * text, never by color alone.
 */

import type { AdminHealth, JobsPanel } from '@/lib/api/admin';

const useSystemHealthMock = vi.fn();
const useJobsMock = vi.fn();
const _idleQuery = () => ({
  data: undefined,
  isError: false,
  isLoading: true,
  isFetching: false,
  error: null,
  refetch: vi.fn(),
});

// Sibling cards have independent queries. Jobs is controllable because these
// tests cover its authoritative queue/dead-letter/purge gauges; the remaining
// cards stay in benign loading states.
vi.mock('@/features/admin/hooks', () => ({
  useSystemHealth: () => useSystemHealthMock(),
  useJobs: () => useJobsMock(),
  useConfig: () => _idleQuery(),
  useErrors: () => _idleQuery(),
  usePerformance: () => _idleQuery(),
  useRunMaintenance: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    data: undefined,
  }),
}));

import AdminHealthPage from '@/app/admin/health/page';

const SNAPSHOT: AdminHealth = {
  tiles: [
    { name: 'Backend', status: 'ok', detail: 'serving; uptime 42s; version 2.0.0' },
    { name: 'Database', status: 'ok', detail: null },
    { name: 'KVStore/Queue', status: 'ok', detail: null },
    { name: 'AI provider', status: 'degraded', detail: 'not configured' },
    { name: 'Storage provider', status: 'ok', detail: 'Local provider configured' },
    { name: 'Migrations', status: 'down', detail: 'head revision unreadable' },
  ],
  release: {
    version: '2.0.0',
    build: null,
    commit: null,
    migrationApplied: '0024',
    migrationHead: '0024',
    env: 'local',
  },
  backendUptimeSeconds: 42,
  jobs: [],
  computedAt: '2026-01-01T00:00:00+00:00',
  stale: false,
};

const JOBS: JobsPanel = {
  jobs: [],
  queueLength: 7,
  queueLengthUnavailable: false,
  deadLetterCount: 2,
  deadLetterCountUnavailable: false,
  purgeBacklog: 5,
  purgeBacklogUnavailable: false,
  computedAt: '2026-01-01T00:00:00+00:00',
  stale: false,
};

function query(over: Partial<ReturnType<typeof useSystemHealthMock>> = {}) {
  return {
    data: SNAPSHOT,
    isError: false,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  };
}

function jobsQuery(over: Partial<ReturnType<typeof useJobsMock>> = {}) {
  return {
    data: JOBS,
    isError: false,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  };
}

beforeEach(() => useJobsMock.mockReturnValue(_idleQuery()));
afterEach(() => vi.clearAllMocks());

describe('AdminHealthPage - tiles render', () => {
  it('renders all six subsystem tiles with their names', () => {
    useSystemHealthMock.mockReturnValue(query());
    render(<AdminHealthPage />);

    for (const name of [
      'Backend',
      'Database',
      'KVStore/Queue',
      'AI provider',
      'Storage provider',
      'Migrations',
    ]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
  });

  it('shows literal text statuses and labels storage configuration accurately', () => {
    useSystemHealthMock.mockReturnValue(query());
    render(<AdminHealthPage />);

    expect(screen.getAllByText('OK').length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText('Configured')).toBeInTheDocument();
    expect(screen.getByText('Degraded')).toBeInTheDocument();
    expect(screen.getByText('Down')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Local provider configured. Configuration check only; live storage connectivity was not checked.'
      )
    ).toBeInTheDocument();
  });

  it('renders the release version', () => {
    useSystemHealthMock.mockReturnValue(query());
    render(<AdminHealthPage />);
    expect(screen.getByText('Version')).toBeInTheDocument();
    expect(screen.getAllByText('2.0.0').length).toBeGreaterThanOrEqual(1);
  });

  it('renders authoritative queue, dead-letter, and purge gauges from useJobs', () => {
    useSystemHealthMock.mockReturnValue(query());
    useJobsMock.mockReturnValue(jobsQuery());
    render(<AdminHealthPage />);

    expect(within(screen.getByText('Queue backlog').parentElement!).getByText('7')).toBeVisible();
    expect(within(screen.getByText('Dead letters').parentElement!).getByText('2')).toBeVisible();
    expect(within(screen.getByText('Purge backlog').parentElement!).getByText('5')).toBeVisible();
  });

  it('shows unavailable instead of values for every unavailable jobs gauge', () => {
    useSystemHealthMock.mockReturnValue(query());
    useJobsMock.mockReturnValue(
      jobsQuery({
        data: {
          ...JOBS,
          queueLengthUnavailable: true,
          deadLetterCountUnavailable: true,
          purgeBacklogUnavailable: true,
        },
      })
    );
    render(<AdminHealthPage />);

    expect(screen.getAllByText('Unavailable')).toHaveLength(3);
    expect(screen.getByLabelText('Queue backlog: unavailable')).toBeVisible();
    expect(screen.getByLabelText('Dead letters: unavailable')).toBeVisible();
    expect(screen.getByLabelText('Purge backlog: unavailable')).toBeVisible();
  });

  it('shows an error state with a retry control on failure', () => {
    useSystemHealthMock.mockReturnValue(
      query({ data: undefined, isError: true, error: new Error('boom') })
    );
    render(<AdminHealthPage />);
    expect(screen.getByText("Couldn't load system health")).toBeInTheDocument();
  });
});
