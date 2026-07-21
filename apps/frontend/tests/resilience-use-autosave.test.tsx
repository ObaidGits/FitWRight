/**
 * Integration tests for the useAutosave linchpin (P4 R2/R3/R4/R5/R7/R8),
 * driving the real controllers + ResilienceStore (MemoryEngine) with a mocked
 * network. Verifies online autosave, conflict surfacing with the correct base
 * (disjoint-merge safety), and offline -> outbox -> reconnect replay.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { MemoryEngine } from '@/lib/resilience/store-engine';
import { ResumeConflictError } from '@/lib/api/resume';

// Mock only updateResume; keep the real error classes for instanceof checks.
const updateResumeMock = vi.fn();
vi.mock('@/lib/api/resume', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/api/resume')>();
  return { ...actual, updateResume: (...args: unknown[]) => updateResumeMock(...args) };
});

import { useAutosave } from '@/lib/hooks/use-autosave';

interface P extends Record<string, unknown> {
  summary: string;
}

const engineFactory = () => ({ engine: new MemoryEngine(), durable: true });

let reachable = true;
beforeEach(() => {
  reachable = true;
  updateResumeMock.mockReset();
  // Reachability probe -> controllable ok/fail.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: reachable, headers: { get: () => null } }) as unknown as Response)
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function setup(overrides: Partial<Parameters<typeof useAutosave>[0]> = {}) {
  return renderHook(() =>
    useAutosave<P>({
      resumeId: 'r1',
      userId: 'u1',
      initialVersion: 1,
      enabled: true,
      debounceMs: 10,
      engineFactory,
      ...overrides,
    })
  );
}

describe('useAutosave - online autosave', () => {
  it('saves a debounced edit via version CAS and reports saved', async () => {
    updateResumeMock.mockResolvedValue({ version: 2, processed_resume: { summary: 'x' } });
    const { result } = setup();
    await waitFor(() => expect(result.current.isLeader).toBe(true)); // no Web Locks -> leader

    act(() => result.current.setBaseVersion(1, { summary: 'base' }));
    act(() => result.current.update({ summary: 'x' }));

    await waitFor(() => expect(updateResumeMock).toHaveBeenCalledTimes(1));
    const [, , opts] = updateResumeMock.mock.calls[0];
    expect((opts as { baseVersion: number }).baseVersion).toBe(1);
    expect((opts as { idempotencyKey: string }).idempotencyKey).toBeTruthy();
    await waitFor(() => expect(result.current.status).toBe('saved'));
  });
});

describe('useAutosave - conflict with correct base', () => {
  it('surfaces a 409 with conflictBase = last synced content (enables safe disjoint merge)', async () => {
    updateResumeMock.mockRejectedValue(new ResumeConflictError(1, 5, { summary: 'server' }));
    const { result } = setup();
    await waitFor(() => expect(result.current.isLeader).toBe(true));

    act(() => result.current.setBaseVersion(1, { summary: 'base' }));
    act(() => result.current.update({ summary: 'mine' }));

    await waitFor(() => expect(result.current.conflict).not.toBeNull());
    expect(result.current.conflict?.currentVersion).toBe(5);
    // The correct common ancestor is exposed so the ConflictDialog can detect
    // overlapping vs disjoint changes (fixes the silent-overwrite defect).
    expect(result.current.conflictBase).toEqual({ summary: 'base' });
    expect(result.current.status).toBe('conflict');
  });
});

describe('useAutosave - offline outbox -> reconnect replay', () => {
  it('queues an edit offline and replays it on reconnect (ordered, CAS)', async () => {
    // Start offline: reachability probe fails.
    reachable = false;
    const { result } = setup();
    await waitFor(() => expect(result.current.isLeader).toBe(true));
    // Force the monitor to observe offline.
    await act(async () => {
      await result.current.flushNow().catch(() => {});
    });

    act(() => result.current.setBaseVersion(1, { summary: 'base' }));
    act(() => result.current.update({ summary: 'offline edit' }));

    // The edit is durably queued to the outbox; no network save yet.
    await waitFor(() => expect(result.current.pendingOutbox).toBeGreaterThan(0));
    expect(updateResumeMock).not.toHaveBeenCalled();

    // Reconnect: probe succeeds -> drain the outbox via CAS replay.
    updateResumeMock.mockResolvedValue({
      version: 2,
      processed_resume: { summary: 'offline edit' },
    });
    reachable = true;
    await act(async () => {
      window.dispatchEvent(new Event('online'));
      // Allow the reachability probe + drain to run.
      await new Promise((r) => setTimeout(r, 50));
    });

    await waitFor(() => expect(updateResumeMock).toHaveBeenCalled());
    await waitFor(() => expect(result.current.pendingOutbox).toBe(0));
  });
});
