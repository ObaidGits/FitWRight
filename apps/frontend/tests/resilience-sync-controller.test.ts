import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { SyncController, type ReplayOutcome } from '@/lib/resilience/sync-controller';
import { ResilienceStore } from '@/lib/resilience/local-store';
import { MemoryEngine } from '@/lib/resilience/store-engine';

async function seed(
  store: ResilienceStore,
  items: Array<{ resumeId: string; v: number; key: string }>
) {
  for (const it of items) {
    await store.appendOutbox(it.resumeId, { v: it.v }, it.v, it.key);
  }
}

describe('SyncController', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('replays entries in FIFO order and clears the outbox', async () => {
    const store = new ResilienceStore(new MemoryEngine(), 'u1');
    await seed(store, [
      { resumeId: 'r1', v: 1, key: 'k1' },
      { resumeId: 'r1', v: 2, key: 'k2' },
      { resumeId: 'r1', v: 3, key: 'k3' },
    ]);
    const order: string[] = [];
    const replay = vi.fn(async (entry, _p): Promise<ReplayOutcome> => {
      order.push(entry.idempotencyKey);
      return { type: 'ok', version: entry.baseVersion! + 1 };
    });
    const sync = new SyncController({ store, replay, isOnline: () => true });
    const pending = await sync.syncOnce();
    expect(order).toEqual(['k1', 'k2', 'k3']);
    expect(pending).toBe(0);
    expect(sync.getStatus()).toBe('synced');
  });

  it('stays offline and does not replay', async () => {
    const store = new ResilienceStore(new MemoryEngine(), 'u1');
    await seed(store, [{ resumeId: 'r1', v: 1, key: 'k1' }]);
    const replay = vi.fn(async (): Promise<ReplayOutcome> => ({ type: 'ok', version: 2 }));
    const sync = new SyncController({ store, replay, isOnline: () => false });
    const pending = await sync.syncOnce();
    expect(replay).not.toHaveBeenCalled();
    expect(pending).toBe(1);
    expect(sync.getStatus()).toBe('offline');
  });

  it('pauses a resource on 409 and raises conflict, continuing other resources', async () => {
    const store = new ResilienceStore(new MemoryEngine(), 'u1');
    await seed(store, [
      { resumeId: 'r1', v: 1, key: 'k1' },
      { resumeId: 'r2', v: 1, key: 'k2' },
    ]);
    const onConflict = vi.fn();
    const replay = vi.fn(async (entry): Promise<ReplayOutcome> => {
      if (entry.resumeId === 'r1') {
        return {
          type: 'conflict',
          info: { yourBaseVersion: 1, currentVersion: 4, currentData: {} },
        };
      }
      return { type: 'ok', version: 2 };
    });
    const sync = new SyncController({ store, replay, isOnline: () => true, onConflict });
    const pending = await sync.syncOnce();
    expect(onConflict).toHaveBeenCalledTimes(1);
    // r2 synced (removed), r1 remains paused.
    expect(pending).toBe(1);
    expect(sync.getStatus()).toBe('conflict');
    const remaining = await store.listOutbox();
    expect(remaining.map((e) => e.resumeId)).toEqual(['r1']);
  });

  it('does not replay a paused resource until resumed', async () => {
    const store = new ResilienceStore(new MemoryEngine(), 'u1');
    await seed(store, [{ resumeId: 'r1', v: 1, key: 'k1' }]);
    let conflictOnce = true;
    const replay = vi.fn(async (): Promise<ReplayOutcome> => {
      if (conflictOnce) {
        conflictOnce = false;
        return {
          type: 'conflict',
          info: { yourBaseVersion: 1, currentVersion: 4, currentData: {} },
        };
      }
      return { type: 'ok', version: 5 };
    });
    const sync = new SyncController({ store, replay, isOnline: () => true });
    await sync.syncOnce();
    expect(sync.getStatus()).toBe('conflict');
    await sync.syncOnce(); // still paused -> no new replay
    expect(replay).toHaveBeenCalledTimes(1);
    sync.resumeResource('r1');
    await sync.syncOnce();
    expect(replay).toHaveBeenCalledTimes(2);
    expect((await store.listOutbox()).length).toBe(0);
  });

  it('retries transient failures with backoff and preserves the entry', async () => {
    const store = new ResilienceStore(new MemoryEngine(), 'u1');
    await seed(store, [{ resumeId: 'r1', v: 1, key: 'k1' }]);
    let attempt = 0;
    const replay = vi.fn(async (): Promise<ReplayOutcome> => {
      attempt += 1;
      return attempt < 2 ? { type: 'transient' } : { type: 'ok', version: 2 };
    });
    // Inject a manual timer so the scheduled retry can be awaited
    // deterministically (WebCrypto hashing is async and not fake-timer bound).
    let scheduled: (() => Promise<number>) | null = null;
    const timers = {
      set: (fn: () => void) => {
        scheduled = fn as unknown as () => Promise<number>;
        return 1;
      },
      clear: () => {},
    };
    const sync = new SyncController({ store, replay, isOnline: () => true, timers });
    await sync.syncOnce();
    expect(sync.getStatus()).toBe('syncing');
    expect((await store.listOutbox())[0].attempts).toBe(1);
    expect(scheduled).not.toBeNull();
    await scheduled!(); // fire the scheduled retry and await its full chain
    expect((await store.listOutbox()).length).toBe(0);
  });

  it('keeps a fatally-failed entry for recovery (never drops)', async () => {
    const store = new ResilienceStore(new MemoryEngine(), 'u1');
    await seed(store, [{ resumeId: 'r1', v: 1, key: 'k1' }]);
    const replay = vi.fn(async (): Promise<ReplayOutcome> => ({ type: 'fatal', message: 'bad' }));
    const sync = new SyncController({ store, replay, isOnline: () => true });
    await sync.syncOnce();
    const remaining = await store.listOutbox();
    expect(remaining.length).toBe(1);
    expect(remaining[0].lastError).toBe('bad');
  });
});
