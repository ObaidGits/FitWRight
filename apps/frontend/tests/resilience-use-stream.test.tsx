/**
 * Integration tests for useStream (P4 R1) and the SW registration controller
 * (R7.1/R9.8) using injected/mocked browser APIs.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { StreamTransport, SseEvent } from '@/lib/resilience/stream-client';

const transportMock = vi.fn();
vi.mock('@/lib/api/resume', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/api/resume')>();
  return {
    ...actual,
    buildResumeStreamTransport: (...args: unknown[]) => transportMock(...args),
  };
});

import { useStream } from '@/lib/hooks/use-stream';

function transportFrom(events: SseEvent[]): StreamTransport {
  return {
    async *open() {
      for (const e of events) yield e;
    },
    cancel: vi.fn(async () => {}),
    fallback: vi.fn(async () => 'FALLBACK'),
  };
}

beforeEach(() => transportMock.mockReset());

describe('useStream', () => {
  it('accumulates streamed tokens progressively and completes', async () => {
    transportMock.mockReturnValue(
      transportFrom([
        { event: 'token', data: { text: 'Dear ' } },
        { event: 'token', data: { text: 'team' } },
        { event: 'done', data: { cancelled: false, text: 'Dear team' } },
      ])
    );
    const { result } = renderHook(() => useStream('r1', { streamingEnabled: true }));
    let final = '';
    await act(async () => {
      final = await result.current.start('cover-letter');
    });
    expect(final).toBe('Dear team');
    expect(result.current.text).toBe('Dear team');
    expect(result.current.status).toBe('done');
  });

  it('uses the non-stream fallback directly when streaming is disabled', async () => {
    const transport = transportFrom([]);
    transportMock.mockReturnValue(transport);
    const { result } = renderHook(() => useStream('r1', { streamingEnabled: false }));
    let final = '';
    await act(async () => {
      final = await result.current.start('cover-letter');
    });
    expect(transport.fallback).toHaveBeenCalled();
    expect(final).toBe('FALLBACK');
    expect(result.current.status).toBe('done');
  });

  it('falls back transparently when the stream errors', async () => {
    transportMock.mockReturnValue(transportFrom([{ event: 'error', data: { message: 'boom' } }]));
    const { result } = renderHook(() => useStream('r1', { streamingEnabled: true }));
    let final = '';
    await act(async () => {
      final = await result.current.start('outreach');
    });
    expect(final).toBe('FALLBACK');
  });
});

// ---------------------------------------------------------------------------
// Service worker registration + safe update (R7.1/R9.8)
// ---------------------------------------------------------------------------
import { registerServiceWorker } from '@/lib/resilience/sw-register';

class FakeSW extends EventTarget {
  postMessage = vi.fn();
}

function fakeRegistration() {
  const listeners: Record<string, (() => void)[]> = {};
  return {
    installing: null as FakeSW | null,
    waiting: null as FakeSW | null,
    active: new FakeSW(),
    addEventListener(type: string, cb: () => void) {
      (listeners[type] ||= []).push(cb);
    },
    _fire(type: string) {
      (listeners[type] || []).forEach((cb) => cb());
    },
    unregister: vi.fn(async () => true),
  };
}

describe('registerServiceWorker', () => {
  let reg: ReturnType<typeof fakeRegistration>;

  beforeEach(() => {
    reg = fakeRegistration();
    vi.stubGlobal('navigator', {
      serviceWorker: {
        register: vi.fn(async () => reg),
        controller: {},
        addEventListener: () => {},
      },
    });
    vi.stubGlobal('caches', { keys: vi.fn(async () => []), delete: vi.fn(async () => true) });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('registers and surfaces an update when a new worker is waiting', async () => {
    reg.waiting = new FakeSW();
    const onUpdateReady = vi.fn();
    const ctrl = await registerServiceWorker({ onUpdateReady });
    expect(ctrl).not.toBeNull();
    expect(onUpdateReady).toHaveBeenCalled();
    // applyUpdate posts SKIP_WAITING to the waiting worker (safe, user-initiated).
    ctrl!.applyUpdate();
    expect(reg.waiting.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' });
  });

  it('kill-switch unregisters and clears caches', async () => {
    const ctrl = await registerServiceWorker();
    await ctrl!.unregisterAndClear();
    expect(reg.unregister).toHaveBeenCalled();
  });

  it('queries the SW for cache hit/miss stats over a MessageChannel', async () => {
    // The active SW replies on the provided MessagePort with stats.
    reg.active.postMessage = vi.fn((_msg: unknown, ports?: MessagePort[]) => {
      ports?.[0]?.postMessage({
        version: 'v1',
        hitRatio: 0.75,
        stats: { staticHit: 3, staticMiss: 1 },
      });
    }) as unknown as typeof reg.active.postMessage;
    const ctrl = await registerServiceWorker();
    const stats = await ctrl!.getCacheStats();
    expect(stats?.hitRatio).toBe(0.75);
    expect(stats?.stats.staticHit).toBe(3);
  });
});
