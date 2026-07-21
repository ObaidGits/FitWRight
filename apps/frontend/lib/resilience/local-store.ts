/**
 * Durable local store - draft + outbox + quarantine (P4 R5, R8, Property 2 & 7).
 *
 * Three logical stores on top of an injected {@link StoreEngine}:
 * - **draft**: the crash safety net - the current editor content, integrity-
 *   hashed and encrypted at rest, one record per resume.
 * - **outbox**: an ordered (FIFO) op-log of edits made offline / while a save
 *   was failing, bounded by entries/bytes so it can never grow unbounded.
 * - **quarantine**: records that failed integrity validation, isolated so they
 *   never load into the live editor, retained for manual export/discard.
 *
 * Cross-cutting: every key is namespaced by `userId` (R8.3); payloads are
 * encrypted with a per-user WebCrypto key (R8.2); reads validate the integrity
 * hash + schema version and quarantine on failure (R8.1); storage failures
 * degrade to an in-memory engine with a warning flag (R8.4).
 */

import {
  cryptoAvailable,
  decryptJSON,
  encryptJSON,
  generateKey,
  type EncryptedPayload,
} from './crypto';
import { SCHEMA_VERSION, contentHash, validateIntegrity, type IntegrityFailure } from './integrity';
import type { StoreEngine, StoreName } from './store-engine';

interface StoredEnvelope {
  schemaVersion: number;
  contentHash: string;
  savedAt: number;
  baseVersion: number | null;
  encrypted: boolean;
  // Exactly one of these is set depending on `encrypted`.
  enc?: EncryptedPayload;
  plain?: unknown;
}

export interface OutboxEntry {
  id: string; // zero-padded monotonic sequence -> FIFO string sort
  userId: string;
  resumeId: string;
  baseVersion: number | null;
  idempotencyKey: string;
  createdAt: number;
  attempts: number;
  lastError: string | null;
  bytes: number;
  // payload envelope (encrypted at rest)
  envelope: StoredEnvelope;
}

export interface QuarantineRecord {
  id: string;
  reason: IntegrityFailure;
  quarantinedAt: number;
  kind: 'draft' | 'outbox';
  resumeId: string | null;
  raw: unknown;
}

export type DraftLoad<T> =
  | { status: 'none' }
  | { status: 'ok'; payload: T; baseVersion: number | null; savedAt: number }
  | { status: 'quarantined'; reason: IntegrityFailure };

export interface OutboxBounds {
  maxEntries: number;
  maxBytes: number;
  /** Fraction of the bound at which the UI should warn (e.g. 0.8). */
  warnRatio: number;
}

export type OutboxPressure = 'ok' | 'warn' | 'full';

export interface AppendResult {
  ok: boolean;
  blocked?: boolean;
  reason?: 'entries' | 'bytes';
  pressure: OutboxPressure;
}

const DEFAULT_BOUNDS: OutboxBounds = {
  maxEntries: 500,
  maxBytes: 5 * 1024 * 1024,
  warnRatio: 0.8,
};

export class ResilienceStore {
  private key: CryptoKey | null = null;
  private encryptionEnabled: boolean;
  private readonly bounds: OutboxBounds;
  private degraded = false;

  constructor(
    private engine: StoreEngine,
    private readonly userId: string,
    opts: { encrypt?: boolean; bounds?: Partial<OutboxBounds> } = {}
  ) {
    this.encryptionEnabled = opts.encrypt !== false && cryptoAvailable();
    this.bounds = { ...DEFAULT_BOUNDS, ...(opts.bounds ?? {}) };
  }

  /** Whether the store degraded to memory-only / unencrypted (drives a warning). */
  isDegraded(): boolean {
    return this.degraded;
  }
  isEncrypted(): boolean {
    return this.encryptionEnabled;
  }

  private ns(id: string): string {
    return `${this.userId}:${id}`;
  }
  private userPrefix(): string {
    return `${this.userId}:`;
  }

  private async getKey(): Promise<CryptoKey | null> {
    if (!this.encryptionEnabled) return null;
    if (this.key) return this.key;
    try {
      const existing = (await this.engine.get('keys', this.ns('key'))) as CryptoKey | undefined;
      if (existing) {
        this.key = existing;
        return existing;
      }
      const fresh = await generateKey();
      await this.engine.set('keys', this.ns('key'), fresh);
      this.key = fresh;
      return fresh;
    } catch {
      // Key store unusable -> disable encryption, degrade with a warning.
      this.encryptionEnabled = false;
      this.degraded = true;
      return null;
    }
  }

  private async makeEnvelope(
    payload: unknown,
    baseVersion: number | null
  ): Promise<StoredEnvelope> {
    const hash = await contentHash(payload);
    const key = await this.getKey();
    const base: StoredEnvelope = {
      schemaVersion: SCHEMA_VERSION,
      contentHash: hash,
      savedAt: Date.now(),
      baseVersion,
      encrypted: false,
    };
    if (key) {
      base.enc = await encryptJSON(key, payload);
      base.encrypted = true;
    } else {
      base.plain = payload;
    }
    return base;
  }

  /** Decode + integrity-check an envelope. Returns payload or an integrity failure. */
  private async openEnvelope<T>(
    env: StoredEnvelope | null | undefined
  ): Promise<{ ok: true; payload: T } | { ok: false; reason: IntegrityFailure }> {
    if (!env || typeof env !== 'object') return { ok: false, reason: 'malformed' };
    let payload: unknown;
    if (env.encrypted) {
      const key = await this.getKey();
      if (!key) return { ok: false, reason: 'decrypt_failure' };
      try {
        payload = await decryptJSON(key, env.enc as EncryptedPayload);
      } catch {
        return { ok: false, reason: 'decrypt_failure' };
      }
    } else {
      payload = env.plain;
    }
    const recomputed = await contentHash(payload);
    const result = validateIntegrity(env, recomputed);
    if (!result.ok) return { ok: false, reason: result.reason ?? 'malformed' };
    return { ok: true, payload: payload as T };
  }

  // -- draft --------------------------------------------------------------

  async saveDraft(resumeId: string, payload: unknown, baseVersion: number | null): Promise<void> {
    const env = await this.makeEnvelope(payload, baseVersion);
    try {
      await this.engine.set('draft', this.ns(`draft:${resumeId}`), env);
    } catch {
      this.degraded = true;
      throw new Error('draft_persist_failed');
    }
  }

  async loadDraft<T>(resumeId: string): Promise<DraftLoad<T>> {
    const key = this.ns(`draft:${resumeId}`);
    const env = (await this.engine.get('draft', key)) as StoredEnvelope | undefined;
    if (!env) return { status: 'none' };
    const opened = await this.openEnvelope<T>(env);
    if (!opened.ok) {
      // Quarantine - never load poison into the editor (R5.3, Property 7).
      await this.quarantine('draft', resumeId, env, opened.reason);
      await this.engine.delete('draft', key);
      return { status: 'quarantined', reason: opened.reason };
    }
    return {
      status: 'ok',
      payload: opened.payload,
      baseVersion: env.baseVersion,
      savedAt: env.savedAt,
    };
  }

  async clearDraft(resumeId: string): Promise<void> {
    await this.engine.delete('draft', this.ns(`draft:${resumeId}`));
  }

  // -- outbox -------------------------------------------------------------

  private async nextSeq(): Promise<number> {
    const cur = (await this.engine.get('meta', this.ns('outbox_seq'))) as number | undefined;
    const next = (typeof cur === 'number' ? cur : 0) + 1;
    await this.engine.set('meta', this.ns('outbox_seq'), next);
    return next;
  }

  async outboxPressure(): Promise<OutboxPressure> {
    const entries = await this.listOutbox();
    const bytes = entries.reduce((sum, e) => sum + e.bytes, 0);
    if (entries.length >= this.bounds.maxEntries || bytes >= this.bounds.maxBytes) return 'full';
    if (
      entries.length >= this.bounds.maxEntries * this.bounds.warnRatio ||
      bytes >= this.bounds.maxBytes * this.bounds.warnRatio
    ) {
      return 'warn';
    }
    return 'ok';
  }

  async appendOutbox(
    resumeId: string,
    payload: unknown,
    baseVersion: number | null,
    idempotencyKey: string
  ): Promise<AppendResult> {
    const entries = await this.listOutbox();
    const bytes = entries.reduce((sum, e) => sum + e.bytes, 0);
    const envelope = await this.makeEnvelope(payload, baseVersion);
    const entryBytes = JSON.stringify(envelope).length;

    // Bounds (R2.5): block new offline edits before overflow - never silently
    // drop queued work.
    if (entries.length + 1 > this.bounds.maxEntries) {
      return { ok: false, blocked: true, reason: 'entries', pressure: 'full' };
    }
    if (bytes + entryBytes > this.bounds.maxBytes) {
      return { ok: false, blocked: true, reason: 'bytes', pressure: 'full' };
    }

    const seq = await this.nextSeq();
    const entry: OutboxEntry = {
      id: String(seq).padStart(12, '0'),
      userId: this.userId,
      resumeId,
      baseVersion,
      idempotencyKey,
      createdAt: Date.now(),
      attempts: 0,
      lastError: null,
      bytes: entryBytes,
      envelope,
    };
    await this.engine.set('outbox', this.ns(`ob:${entry.id}`), entry);
    return { ok: true, pressure: await this.outboxPressure() };
  }

  /** Outbox entries in FIFO order (ascending sequence). */
  async listOutbox(): Promise<OutboxEntry[]> {
    const rows = await this.engine.entries('outbox', this.ns('ob:'));
    return rows.map((r) => r.value as OutboxEntry).sort((a, b) => a.id.localeCompare(b.id));
  }

  /** Decode an outbox entry's payload, quarantining it on integrity failure. */
  async openOutboxPayload<T>(
    entry: OutboxEntry
  ): Promise<{ ok: true; payload: T } | { ok: false; reason: IntegrityFailure }> {
    const opened = await this.openEnvelope<T>(entry.envelope);
    if (!opened.ok) {
      await this.quarantine('outbox', entry.resumeId, entry, opened.reason);
      await this.removeOutbox(entry.id);
    }
    return opened;
  }

  async removeOutbox(id: string): Promise<void> {
    await this.engine.delete('outbox', this.ns(`ob:${id}`));
  }

  async recordOutboxAttempt(id: string, error: string | null): Promise<void> {
    const entry = (await this.engine.get('outbox', this.ns(`ob:${id}`))) as OutboxEntry | undefined;
    if (!entry) return;
    entry.attempts += 1;
    entry.lastError = error;
    await this.engine.set('outbox', this.ns(`ob:${id}`), entry);
  }

  // -- quarantine ---------------------------------------------------------

  private async quarantine(
    kind: 'draft' | 'outbox',
    resumeId: string | null,
    raw: unknown,
    reason: IntegrityFailure
  ): Promise<void> {
    const id = `${kind}:${resumeId ?? 'unknown'}:${Date.now()}`;
    const record: QuarantineRecord = {
      id,
      reason,
      quarantinedAt: Date.now(),
      kind,
      resumeId,
      raw,
    };
    try {
      await this.engine.set('quarantine', this.ns(`q:${id}`), record);
    } catch {
      /* even quarantine failing must not crash the caller */
    }
  }

  async listQuarantine(): Promise<QuarantineRecord[]> {
    const rows = await this.engine.entries('quarantine', this.ns('q:'));
    return rows.map((r) => r.value as QuarantineRecord);
  }

  async discardQuarantine(id: string): Promise<void> {
    await this.engine.delete('quarantine', this.ns(`q:${id}`));
  }

  // -- lifecycle ----------------------------------------------------------

  /** Wipe ALL of this user's local data (logout / different-user detection, R8.2). */
  async clearUser(): Promise<void> {
    this.key = null;
    const stores: StoreName[] = ['draft', 'outbox', 'quarantine', 'keys', 'meta'];
    for (const store of stores) {
      try {
        await this.engine.deletePrefix(store, this.userPrefix());
      } catch {
        /* best-effort */
      }
    }
  }
}
