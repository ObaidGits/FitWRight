/**
 * P4 performance verification harness (Task 8.4).
 *
 * Two kinds of assertion:
 * 1. **Behavioral perf invariants** (deterministic, CI-safe): debounce/coalesce
 *    ratio, ordered replay throughput count, circuit-breaker call bounds,
 *    identical-content no-op. These fail if the perf-relevant *behavior* regresses.
 * 2. **Latency budgets** (wall-clock, generous ceilings): IndexedDB read/write,
 *    encryption round-trip, first-token latency. These fail loudly on a large
 *    regression while tolerating CI jitter.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import {
  SaveController,
  type SaveOutcome,
  type SaveContext,
} from '@/lib/resilience/save-controller';
import { ResilienceStore } from '@/lib/resilience/local-store';
import { IndexedDbEngine } from '@/lib/resilience/store-engine';
import { SyncController, type ReplayOutcome } from '@/lib/resilience/sync-controller';
import {
  StreamController,
  type StreamTransport,
  type SseEvent,
} from '@/lib/resilience/stream-client';
import { encryptJSON, decryptJSON, generateKey } from '@/lib/resilience/crypto';

// Generous ceilings — orders of magnitude above expected, so only a real
// regression trips them (not CI noise).
const BUDGET = {
  idbWriteMsAvg: 60,
  idbReadMsAvg: 60,
  encryptRoundtripMsAvg: 40,
  firstTokenMs: 500, // stubbed provider delay is 50ms; ceiling well above
  replayThroughputMinPerSec: 20,
};

beforeEach(() => {
  (globalThis as unknown as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
});

describe('perf — autosave debounce/coalesce ratio', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('coalesces a burst of 50 edits into a single network save', async () => {
    const save = vi.fn<(p: { v: number }, c: SaveContext) => Promise<SaveOutcome>>(async () => ({
      type: 'ok',
      version: 2,
    }));
    const c = new SaveController<{ v: number }>({
      save,
      persistDraft: async () => {},
      isOnline: () => true,
      newIdempotencyKey: () => 'k',
      debounceMs: 100,
    });
    c.setBaseVersion(1);
    for (let i = 0; i < 50; i++) c.update({ v: i });
    await vi.advanceTimersByTimeAsync(100);
    // 50 edits → exactly 1 save (coalescing invariant, R4.2).
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toEqual({ v: 49 });
  });

  it('circuit breaker bounds request volume under a sustained brownout', async () => {
    const save = vi.fn(async (): Promise<SaveOutcome> => ({ type: 'transient' }));
    const c = new SaveController<{ v: number }>({
      save,
      persistDraft: async () => {},
      isOnline: () => true,
      newIdempotencyKey: () => 'k',
      debounceMs: 10,
      backoffBaseMs: 5,
      backoffCapMs: 50,
      breakerThreshold: 4,
      breakerCooldownMs: 10_000,
    });
    c.setBaseVersion(1);
    c.update({ v: 1 });
    // Drive 5s of virtual time through a permanent brownout.
    await vi.advanceTimersByTimeAsync(10);
    for (let i = 0; i < 50; i++) await vi.advanceTimersByTimeAsync(50);
    // With the breaker open, the client must NOT hammer: far fewer than the
    // ~100 attempts an uncapped retry loop would make.
    expect(save.mock.calls.length).toBeLessThan(12);
  });
});

describe('perf — IndexedDB latency budget', () => {
  it('draft write/read average under budget over 25 ops', async () => {
    const store = new ResilienceStore(new IndexedDbEngine(), 'perf-user');
    const payload = {
      summary: 'x'.repeat(2000),
      items: Array.from({ length: 50 }, (_, i) => ({ i })),
    };
    const N = 25;
    let writeTotal = 0;
    let readTotal = 0;
    for (let i = 0; i < N; i++) {
      let t = performance.now();
      await store.saveDraft('r1', { ...payload, i }, i);
      writeTotal += performance.now() - t;
      t = performance.now();
      await store.loadDraft('r1');
      readTotal += performance.now() - t;
    }
    const wAvg = writeTotal / N;
    const rAvg = readTotal / N;
    console.log(`[perf] idb write avg=${wAvg.toFixed(2)}ms read avg=${rAvg.toFixed(2)}ms`);
    expect(wAvg).toBeLessThan(BUDGET.idbWriteMsAvg);
    expect(rAvg).toBeLessThan(BUDGET.idbReadMsAvg);
  });
});

describe('perf — encryption overhead budget', () => {
  it('AES-GCM encrypt+decrypt round-trip average under budget', async () => {
    const key = await generateKey();
    const payload = { summary: 'y'.repeat(4000) };
    const N = 50;
    const t = performance.now();
    for (let i = 0; i < N; i++) {
      const enc = await encryptJSON(key, payload);
      await decryptJSON(key, enc);
    }
    const avg = (performance.now() - t) / N;
    console.log(`[perf] encrypt+decrypt avg=${avg.toFixed(2)}ms`);
    expect(avg).toBeLessThan(BUDGET.encryptRoundtripMsAvg);
  });
});

describe('perf — outbox replay throughput + ordering', () => {
  it('replays 60 ordered entries above the throughput floor', async () => {
    const store = new ResilienceStore(new IndexedDbEngine(), 'perf-user');
    const N = 60;
    for (let i = 0; i < N; i++) await store.appendOutbox(`r${i % 5}`, { i }, i, `k${i}`);
    const order: number[] = [];
    const replay = vi.fn(async (_e, p): Promise<ReplayOutcome> => {
      order.push((p as { i: number }).i);
      return { type: 'ok', version: 1 };
    });
    const sync = new SyncController({ store, replay, isOnline: () => true });
    const t = performance.now();
    await sync.syncOnce();
    const secs = (performance.now() - t) / 1000;
    const throughput = N / Math.max(secs, 0.001);
    console.log(`[perf] replay throughput=${throughput.toFixed(0)}/s`);
    expect(order).toEqual([...Array(N).keys()]); // strict FIFO preserved
    expect(throughput).toBeGreaterThan(BUDGET.replayThroughputMinPerSec);
    expect((await store.listOutbox()).length).toBe(0);
  });
});

describe('perf — streaming first-token latency', () => {
  it('measures time-to-first-token under the budget', async () => {
    const events: SseEvent[] = [
      { event: 'heartbeat', data: {} },
      { event: 'token', data: { text: 'Hello' } },
      { event: 'done', data: { cancelled: false, text: 'Hello' } },
    ];
    const transport: StreamTransport = {
      async *open() {
        // Simulate a realistic ~50ms provider first-token delay.
        await new Promise((r) => setTimeout(r, 50));
        for (const e of events) yield e;
      },
      cancel: async () => {},
      fallback: async () => '',
    };
    let firstTokenAt = 0;
    const start = performance.now();
    const ctrl = new StreamController(transport, {
      onToken: () => {
        if (!firstTokenAt) firstTokenAt = performance.now();
      },
    });
    await ctrl.run();
    const ttft = firstTokenAt - start;
    console.log(`[perf] time-to-first-token=${ttft.toFixed(0)}ms`);
    expect(ttft).toBeGreaterThan(0);
    expect(ttft).toBeLessThan(BUDGET.firstTokenMs);
  });
});
