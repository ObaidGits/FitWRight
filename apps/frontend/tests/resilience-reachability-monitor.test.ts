/**
 * Tests for ReachabilityMonitor debounce (fixes the false "Offline" banner).
 *
 * The bug: a single slow/failed /health probe (busy single-worker backend,
 * cold start, or a spurious browser `offline` event) flipped the banner to
 * offline even with a strong connection. The fix requires TWO consecutive
 * failed probes before going offline, and a single success clears it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ReachabilityMonitor } from '@/lib/resilience/reachability';

describe('ReachabilityMonitor - debounced offline', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('does NOT flip offline on a single failed probe', async () => {
    const fetchFn = vi.fn(async () => {
      throw new Error('slow probe');
    });
    const monitor = new ReachabilityMonitor({ fetchFn, failureThreshold: 2 });
    const seen: boolean[] = [];
    monitor.subscribe((r) => seen.push(r));

    // Starts optimistic (reachable) and stays reachable after one miss.
    const ok = await monitor.check();
    expect(ok).toBe(false);
    expect(monitor.isReachable()).toBe(true);
    // No listener fired because state didn't change.
    expect(seen).toEqual([]);
  });

  it('flips offline after two consecutive failed probes', async () => {
    const fetchFn = vi.fn(async () => {
      throw new Error('down');
    });
    const monitor = new ReachabilityMonitor({ fetchFn, failureThreshold: 2 });
    const seen: boolean[] = [];
    monitor.subscribe((r) => seen.push(r));

    await monitor.check();
    expect(monitor.isReachable()).toBe(true);
    await monitor.check();
    expect(monitor.isReachable()).toBe(false);
    expect(seen).toEqual([false]);
  });

  it('a single success clears the failure counter and stays online', async () => {
    let fail = true;
    const fetchFn = vi.fn(async () => {
      if (fail) throw new Error('blip');
      return { ok: true } as Response;
    });
    const monitor = new ReachabilityMonitor({ fetchFn, failureThreshold: 2 });

    await monitor.check(); // 1 miss, still online
    expect(monitor.isReachable()).toBe(true);
    fail = false;
    await monitor.check(); // success resets counter
    expect(monitor.isReachable()).toBe(true);

    // A fresh miss after a success must NOT immediately go offline.
    fail = true;
    await monitor.check();
    expect(monitor.isReachable()).toBe(true);
  });

  it('recovers to online immediately on a success after being offline', async () => {
    let fail = true;
    const fetchFn = vi.fn(async () => {
      if (fail) throw new Error('down');
      return { ok: true } as Response;
    });
    const monitor = new ReachabilityMonitor({ fetchFn, failureThreshold: 2 });

    await monitor.check();
    await monitor.check();
    expect(monitor.isReachable()).toBe(false);

    fail = false;
    await monitor.check();
    expect(monitor.isReachable()).toBe(true);
  });
});
