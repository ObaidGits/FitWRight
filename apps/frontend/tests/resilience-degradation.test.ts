import { describe, it, expect, vi } from 'vitest';
import {
  computeDegradation,
  capabilitiesFor,
  describeDegradation,
  type DegradationSignals,
} from '@/lib/resilience/degradation';
import { probeReachability } from '@/lib/resilience/reachability';

const base: DegradationSignals = {
  backendReachable: true,
  aiAvailable: true,
  streamingAvailable: true,
  storageOk: true,
  apiVersionSkew: false,
};

describe('computeDegradation', () => {
  it('is full when everything is healthy', () => {
    expect(computeDegradation(base)).toBe('full');
    expect(capabilitiesFor('full').ai).toBe(true);
  });

  it('is degraded-ai when AI is down but backend reachable', () => {
    expect(computeDegradation({ ...base, aiAvailable: false })).toBe('degraded-ai');
    const caps = capabilitiesFor('degraded-ai');
    expect(caps.editResume).toBe(true);
    expect(caps.serverSave).toBe(true);
    expect(caps.ai).toBe(false);
  });

  it('is offline-read-write when unreachable but storage works', () => {
    expect(computeDegradation({ ...base, backendReachable: false })).toBe('offline-read-write');
    const caps = capabilitiesFor('offline-read-write');
    expect(caps.editResume).toBe(true);
    expect(caps.serverSave).toBe(false);
    expect(caps.networkFeatures).toBe(false);
  });

  it('is read-only when offline AND storage is unavailable', () => {
    expect(computeDegradation({ ...base, backendReachable: false, storageOk: false })).toBe(
      'read-only'
    );
    expect(capabilitiesFor('read-only').editResume).toBe(false);
  });

  it('is safe-mode on API version skew regardless of other signals', () => {
    expect(computeDegradation({ ...base, apiVersionSkew: true })).toBe('safe-mode');
    expect(computeDegradation({ ...base, backendReachable: false, apiVersionSkew: true })).toBe(
      'safe-mode'
    );
    const caps = capabilitiesFor('safe-mode');
    expect(caps.serverSave).toBe(false);
    expect(caps.read).toBe(true);
  });

  it('provides a human description for every level', () => {
    for (const level of [
      'full',
      'degraded-ai',
      'offline-read-write',
      'read-only',
      'safe-mode',
    ] as const) {
      expect(describeDegradation(level).label).toBeTruthy();
    }
  });
});

describe('probeReachability', () => {
  it('returns true on a 2xx within the timeout', async () => {
    const fetchFn = vi.fn(async () => ({ ok: true }) as Response);
    expect(await probeReachability({ fetchFn, url: '/api/v1/health' })).toBe(true);
    expect(fetchFn).toHaveBeenCalled();
  });

  it('returns false on a non-2xx', async () => {
    const fetchFn = vi.fn(async () => ({ ok: false }) as Response);
    expect(await probeReachability({ fetchFn })).toBe(false);
  });

  it('returns false when the fetch throws (backend down / abort)', async () => {
    const fetchFn = vi.fn(async () => {
      throw new Error('network');
    });
    expect(await probeReachability({ fetchFn })).toBe(false);
  });
});
