/**
 * Harvest pacing.
 *
 * This exists to keep a user's LinkedIn or Naukri account from being restricted,
 * so the properties that matter are the ones that cannot be configured away: a
 * daily cap that actually stops runs, and gaps that are never perfectly regular.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_DAILY_CAP,
  MIN_GAP_MS,
  jitteredGap,
  recordRun,
  remainingToday,
  usageSummary,
} from '@/lib/pacing';

/** In-memory stand-in for chrome.storage.local. */
function installFakeStorage(): void {
  const store: Record<string, unknown> = {};
  (globalThis as unknown as { chrome: unknown }).chrome = {
    storage: {
      local: {
        get: async (key: string) => ({ [key]: store[key] }),
        set: async (patch: Record<string, unknown>) => {
          Object.assign(store, patch);
        },
      },
    },
  };
}

describe('daily cap', () => {
  beforeEach(installFakeStorage);

  it('starts with the full allowance', async () => {
    expect(await remainingToday('hirist')).toBe(DEFAULT_DAILY_CAP);
  });

  it('counts down as runs are recorded', async () => {
    await recordRun('hirist');
    await recordRun('hirist');
    expect(await remainingToday('hirist')).toBe(DEFAULT_DAILY_CAP - 2);
  });

  it('reaches zero and never goes negative', async () => {
    for (let i = 0; i < DEFAULT_DAILY_CAP + 4; i += 1) await recordRun('hirist');
    expect(await remainingToday('hirist')).toBe(0);
  });

  it('tracks each board separately', async () => {
    await recordRun('hirist');
    expect(await remainingToday('naukri')).toBe(DEFAULT_DAILY_CAP);
  });

  it('resets when the calendar day changes', async () => {
    await recordRun('hirist');
    expect(await remainingToday('hirist')).toBe(DEFAULT_DAILY_CAP - 1);

    // Same storage, a different day: yesterday's tally must not carry over.
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    vi.setSystemTime(tomorrow);
    expect(await remainingToday('hirist')).toBe(DEFAULT_DAILY_CAP);
    vi.useRealTimers();
  });

  it('reports usage per board for today', async () => {
    await recordRun('hirist');
    await recordRun('hirist');
    await recordRun('naukri');
    expect(await usageSummary()).toEqual({ hirist: 2, naukri: 1 });
  });
});

describe('jittered gaps', () => {
  it('never returns less than the floor', () => {
    for (let i = 0; i < 200; i += 1) {
      expect(jitteredGap(100)).toBeGreaterThanOrEqual(MIN_GAP_MS);
    }
  });

  it('varies, so a run has no machine-regular rhythm', () => {
    const seen = new Set<number>();
    for (let i = 0; i < 50; i += 1) seen.add(jitteredGap(2500));
    // A fixed interval is a fingerprint; anything above a couple of values proves
    // this is not one.
    expect(seen.size).toBeGreaterThan(5);
  });

  it('stays in a sane band around the requested midpoint', () => {
    for (let i = 0; i < 100; i += 1) {
      const gap = jitteredGap(2500);
      expect(gap).toBeGreaterThanOrEqual(1500);
      expect(gap).toBeLessThanOrEqual(4000);
    }
  });
});
