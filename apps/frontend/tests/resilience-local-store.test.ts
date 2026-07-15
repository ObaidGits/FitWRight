import { describe, it, expect } from 'vitest';
import { ResilienceStore } from '@/lib/resilience/local-store';
import { MemoryEngine } from '@/lib/resilience/store-engine';
import { cryptoAvailable } from '@/lib/resilience/crypto';

function store(engine = new MemoryEngine(), userId = 'user-1', opts = {}) {
  return { engine, store: new ResilienceStore(engine, userId, opts) };
}

describe('ResilienceStore draft', () => {
  it('round-trips a draft (encrypt at rest when available)', async () => {
    const { store: s } = store();
    await s.saveDraft('r1', { summary: 'hello' }, 3);
    const load = await s.loadDraft<{ summary: string }>('r1');
    expect(load.status).toBe('ok');
    if (load.status === 'ok') {
      expect(load.payload).toEqual({ summary: 'hello' });
      expect(load.baseVersion).toBe(3);
    }
  });

  it('reports encryption enabled when WebCrypto is available', () => {
    const { store: s } = store();
    expect(s.isEncrypted()).toBe(cryptoAvailable());
  });

  it('returns none when no draft exists', async () => {
    const { store: s } = store();
    expect((await s.loadDraft('missing')).status).toBe('none');
  });

  it('quarantines a corrupted draft and never loads it', async () => {
    const engine = new MemoryEngine();
    const { store: s } = store(engine);
    await s.saveDraft('r1', { summary: 'good' }, 1);
    // Tamper with the stored envelope's hash to simulate corruption.
    const key = 'user-1:draft:r1';
    const env = (await engine.get('draft', key)) as { contentHash: string };
    env.contentHash = 'deadbeef';
    await engine.set('draft', key, env);

    const load = await s.loadDraft('r1');
    expect(load.status).toBe('quarantined');
    // The bad draft is removed from the live store and moved to quarantine.
    expect(await engine.get('draft', key)).toBeUndefined();
    const q = await s.listQuarantine();
    expect(q).toHaveLength(1);
    expect(q[0].kind).toBe('draft');
    expect(q[0].reason).toBe('hash_mismatch');
  });

  it('clears a draft', async () => {
    const { store: s } = store();
    await s.saveDraft('r1', { summary: 'x' }, 1);
    await s.clearDraft('r1');
    expect((await s.loadDraft('r1')).status).toBe('none');
  });
});

describe('ResilienceStore outbox', () => {
  it('appends and lists entries in FIFO order', async () => {
    const { store: s } = store();
    await s.appendOutbox('r1', { v: 1 }, 1, 'k1');
    await s.appendOutbox('r1', { v: 2 }, 2, 'k2');
    await s.appendOutbox('r1', { v: 3 }, 3, 'k3');
    const entries = await s.listOutbox();
    expect(entries.map((e) => e.idempotencyKey)).toEqual(['k1', 'k2', 'k3']);
  });

  it('decrypts outbox payloads and preserves order across many entries', async () => {
    const { store: s } = store();
    for (let i = 0; i < 15; i++) await s.appendOutbox('r1', { i }, i, `k${i}`);
    const entries = await s.listOutbox();
    // Lexicographic sort of zero-padded ids must equal numeric order.
    const payloads: number[] = [];
    for (const e of entries) {
      const opened = await s.openOutboxPayload<{ i: number }>(e);
      expect(opened.ok).toBe(true);
      if (opened.ok) payloads.push(opened.payload.i);
    }
    expect(payloads).toEqual([...Array(15).keys()]);
  });

  it('blocks new entries at the entry cap (never silently drops)', async () => {
    const { store: s } = store(new MemoryEngine(), 'user-1', {
      bounds: { maxEntries: 2, maxBytes: 10_000_000, warnRatio: 0.5 },
    });
    expect((await s.appendOutbox('r1', { v: 1 }, 1, 'k1')).ok).toBe(true);
    const second = await s.appendOutbox('r1', { v: 2 }, 2, 'k2');
    expect(second.ok).toBe(true);
    expect(second.pressure).toBe('full'); // at cap
    const third = await s.appendOutbox('r1', { v: 3 }, 3, 'k3');
    expect(third.ok).toBe(false);
    expect(third.blocked).toBe(true);
    expect(third.reason).toBe('entries');
    // Queued work is preserved.
    expect((await s.listOutbox()).length).toBe(2);
  });

  it('removes and records attempts on outbox entries', async () => {
    const { store: s } = store();
    await s.appendOutbox('r1', { v: 1 }, 1, 'k1');
    const [entry] = await s.listOutbox();
    await s.recordOutboxAttempt(entry.id, 'network error');
    const [updated] = await s.listOutbox();
    expect(updated.attempts).toBe(1);
    expect(updated.lastError).toBe('network error');
    await s.removeOutbox(entry.id);
    expect((await s.listOutbox()).length).toBe(0);
  });
});

describe('ResilienceStore namespacing + logout', () => {
  it('never mixes one user’s data into another’s', async () => {
    const engine = new MemoryEngine();
    const a = new ResilienceStore(engine, 'user-a');
    const b = new ResilienceStore(engine, 'user-b');
    await a.saveDraft('r1', { who: 'a' }, 1);
    await b.saveDraft('r1', { who: 'b' }, 1);
    const la = await a.loadDraft<{ who: string }>('r1');
    const lb = await b.loadDraft<{ who: string }>('r1');
    expect(la.status === 'ok' && la.payload.who).toBe('a');
    expect(lb.status === 'ok' && lb.payload.who).toBe('b');
  });

  it('clearUser wipes only that user’s data', async () => {
    const engine = new MemoryEngine();
    const a = new ResilienceStore(engine, 'user-a');
    const b = new ResilienceStore(engine, 'user-b');
    await a.saveDraft('r1', { who: 'a' }, 1);
    await a.appendOutbox('r1', { v: 1 }, 1, 'k1');
    await b.saveDraft('r1', { who: 'b' }, 1);

    await a.clearUser();
    expect((await a.loadDraft('r1')).status).toBe('none');
    expect((await a.listOutbox()).length).toBe(0);
    // user-b is untouched.
    expect((await b.loadDraft('r1')).status).toBe('ok');
  });
});
