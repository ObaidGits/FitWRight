/**
 * Real IndexedDB integration for the durable store (P4 R8) using fake-indexeddb
 * (a spec-compliant in-memory IDB), so the actual IndexedDbEngine transactions,
 * cursor iteration, prefix delete, and the ResilienceStore running over it are
 * exercised — not just the MemoryEngine.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { IndexedDbEngine, indexedDbAvailable } from '@/lib/resilience/store-engine';
import { ResilienceStore } from '@/lib/resilience/local-store';

beforeEach(() => {
  // Fresh IDB per test for isolation.
  (globalThis as unknown as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
});

describe('IndexedDbEngine (real IDB via fake-indexeddb)', () => {
  it('is detected as available', () => {
    expect(indexedDbAvailable()).toBe(true);
  });

  it('round-trips get/set/delete across object stores', async () => {
    const e = new IndexedDbEngine();
    await e.set('draft', 'k1', { a: 1 });
    expect(await e.get('draft', 'k1')).toEqual({ a: 1 });
    await e.delete('draft', 'k1');
    expect(await e.get('draft', 'k1')).toBeUndefined();
  });

  it('iterates entries and deletes by prefix', async () => {
    const e = new IndexedDbEngine();
    await e.set('outbox', 'u1:ob:1', { v: 1 });
    await e.set('outbox', 'u1:ob:2', { v: 2 });
    await e.set('outbox', 'u2:ob:1', { v: 3 });
    const u1 = await e.entries('outbox', 'u1:');
    expect(u1.map((r) => r.key).sort()).toEqual(['u1:ob:1', 'u1:ob:2']);
    await e.deletePrefix('outbox', 'u1:');
    expect((await e.entries('outbox', 'u1:')).length).toBe(0);
    // u2 untouched (cross-user isolation).
    expect((await e.entries('outbox', 'u2:')).length).toBe(1);
  });
});

describe('ResilienceStore over real IDB', () => {
  it('persists an encrypted draft across engine instances (survives reload)', async () => {
    const s1 = new ResilienceStore(new IndexedDbEngine(), 'user-1');
    await s1.saveDraft('r1', { summary: 'persist me' }, 4);
    // New engine instance = simulated page reload against the same IDB.
    const s2 = new ResilienceStore(new IndexedDbEngine(), 'user-1');
    const load = await s2.loadDraft<{ summary: string }>('r1');
    expect(load.status).toBe('ok');
    if (load.status === 'ok') {
      expect(load.payload.summary).toBe('persist me');
      expect(load.baseVersion).toBe(4);
    }
  });

  it('quarantines a tampered draft read from real IDB', async () => {
    const engine = new IndexedDbEngine();
    const s = new ResilienceStore(engine, 'user-1');
    await s.saveDraft('r1', { summary: 'good' }, 1);
    const env = (await engine.get('draft', 'user-1:draft:r1')) as Record<string, unknown>;
    env.contentHash = 'tampered';
    await engine.set('draft', 'user-1:draft:r1', env);
    const load = await s.loadDraft('r1');
    expect(load.status).toBe('quarantined');
    expect((await s.listQuarantine()).length).toBe(1);
  });

  it('appends + replays outbox entries in FIFO order over real IDB', async () => {
    const s = new ResilienceStore(new IndexedDbEngine(), 'user-1');
    await s.appendOutbox('r1', { v: 1 }, 1, 'k1');
    await s.appendOutbox('r1', { v: 2 }, 2, 'k2');
    const entries = await s.listOutbox();
    expect(entries.map((e) => e.idempotencyKey)).toEqual(['k1', 'k2']);
    const opened = await s.openOutboxPayload<{ v: number }>(entries[0]);
    expect(opened.ok && opened.payload.v).toBe(1);
  });

  it('clearUser wipes all of that user’s stores in real IDB', async () => {
    const engine = new IndexedDbEngine();
    const a = new ResilienceStore(engine, 'user-a');
    const b = new ResilienceStore(engine, 'user-b');
    await a.saveDraft('r1', { x: 1 }, 1);
    await a.appendOutbox('r1', { x: 1 }, 1, 'k1');
    await b.saveDraft('r1', { y: 2 }, 1);
    await a.clearUser();
    expect((await a.loadDraft('r1')).status).toBe('none');
    expect((await a.listOutbox()).length).toBe(0);
    expect((await b.loadDraft('r1')).status).toBe('ok');
  });
});
