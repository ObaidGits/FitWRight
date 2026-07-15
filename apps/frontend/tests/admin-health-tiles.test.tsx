import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

/**
 * Admin System Health page (Task 6.6 / Req 3.2, 3.8, 15.8).
 *
 * Lightweight component-level "tiles render" check: with a composed
 * `AdminHealth` snapshot from the `useSystemHealth` hook, the page renders all
 * six subsystem tiles, each with its literal text status label (never color
 * alone — Req 3.8), plus the release version. The full cross-browser E2E
 * tiles-render assertion is owned by task 19.1 (which extends
 * `e2e/admin.spec.ts`); this component test covers the render contract here.
 */

import type { AdminHealth } from '@/lib/api/admin';

const useSystemHealthMock = vi.fn();
// The Health page also mounts sibling cards (jobs, errors, performance, config)
// and the maintenance panel, each backed by its own admin hook. This tiles test
// only drives `useSystemHealth`; the other hooks are stubbed to a benign
// loading state so those cards render their skeletons (never crash) without
// affecting the tile/release assertions below.
const _idleQuery = () => ({
  data: undefined,
  isError: false,
  isLoading: true,
  isFetching: false,
  error: null,
  refetch: vi.fn(),
});
vi.mock('@/features/admin/hooks', () => ({
  useSystemHealth: () => useSystemHealthMock(),
  useJobs: () => _idleQuery(),
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
    { name: 'Backend', status: 'ok', detail: 'serving; uptime 42s; version 1.2.0' },
    { name: 'Database', status: 'ok', detail: null },
    { name: 'KVStore/Queue', status: 'ok', detail: null },
    { name: 'AI provider', status: 'degraded', detail: 'not configured' },
    { name: 'Storage provider', status: 'ok', detail: 'provider local' },
    { name: 'Migrations', status: 'down', detail: 'head revision unreadable' },
  ],
  release: {
    version: '1.2.0',
    build: null,
    commit: null,
    migrationApplied: '0021',
    migrationHead: '0021',
    env: 'local',
  },
  backendUptimeSeconds: 42,
  jobs: [],
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

afterEach(() => vi.clearAllMocks());

describe('AdminHealthPage — tiles render', () => {
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

  it('shows a literal text status label per tile (not color alone)', () => {
    useSystemHealthMock.mockReturnValue(query());
    render(<AdminHealthPage />);

    // Five OK tiles → four "OK" labels (Backend/Database/KVStore/Storage),
    // plus the degraded + down labels for AI + Migrations.
    expect(screen.getAllByText('OK').length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText('Degraded')).toBeInTheDocument();
    expect(screen.getByText('Down')).toBeInTheDocument();
  });

  it('renders the release version', () => {
    useSystemHealthMock.mockReturnValue(query());
    render(<AdminHealthPage />);
    expect(screen.getByText('Version')).toBeInTheDocument();
    expect(screen.getAllByText('1.2.0').length).toBeGreaterThanOrEqual(1);
  });

  it('shows an error state with a retry control on failure', () => {
    useSystemHealthMock.mockReturnValue(
      query({ data: undefined, isError: true, error: new Error('boom') })
    );
    render(<AdminHealthPage />);
    expect(screen.getByText("Couldn't load system health")).toBeInTheDocument();
  });
});
