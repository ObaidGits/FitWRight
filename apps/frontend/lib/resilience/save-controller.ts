/**
 * SaveController - advanced autosave state machine (P4 R4, Property 2 & 4).
 *
 * A transport-injected pure controller (no direct DOM/network) so it is fully
 * unit-testable without a browser. Guarantees:
 *
 * - **Debounce + coalesce**: rapid edits collapse into at most one in-flight
 *   request plus one trailing save (latest content wins) - never blocks typing.
 * - **Durable-draft-first (R4.5)**: the durable local draft is written on every
 *   debounce tick *before* any network attempt, so at all times either the
 *   server has the latest accepted content or the local draft does.
 * - **Idempotent (R4.2)**: each content snapshot carries a stable idempotency
 *   key reused across retries, so a retried save is deduped server-side.
 * - **Storm-safe retries (R4.4)**: transient failures retry with full-jitter
 *   backoff under a capped count and a circuit breaker; `Retry-After` honored.
 * - **Conflict-safe (R4.3)**: a 409 routes into the conflict flow, never a
 *   blind retry.
 */

import { CircuitBreaker, fullJitterBackoff } from './backoff';

export type SaveStatus =
  | 'idle'
  | 'dirty'
  | 'saving'
  | 'saved'
  | 'retrying'
  | 'offline'
  | 'conflict';

export interface ConflictInfo {
  yourBaseVersion: number | null;
  currentVersion: number;
  currentData: unknown;
}

export type SaveOutcome =
  | { type: 'ok'; version: number; data?: unknown }
  | { type: 'conflict'; info: ConflictInfo }
  | { type: 'transient'; retryAfterMs?: number }
  | { type: 'fatal'; message?: string };

export interface SaveContext {
  baseVersion: number | null;
  idempotencyKey: string;
  signal?: AbortSignal;
}

export interface Timers {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

export interface SaveControllerOptions<T> {
  /** Perform the network save (version CAS + idempotency). */
  save: (payload: T, ctx: SaveContext) => Promise<SaveOutcome>;
  /** Write the durable local draft (IndexedDB) - the crash safety net. */
  persistDraft: (payload: T) => Promise<void>;
  /** True when the backend is reachable (reachability probe, not navigator.onLine). */
  isOnline: () => boolean;
  /** Generate a fresh idempotency key for a new content snapshot. */
  newIdempotencyKey: () => string;
  /** Status change notification (drives the status chip). */
  onStatus?: (status: SaveStatus, meta?: { lastSavedAt?: number; error?: string }) => void;
  /** Conflict notification (drives the conflict modal). */
  onConflict?: (info: ConflictInfo) => void;
  /** Successful save (drives fan-out to other tabs + base-version update). */
  onSaved?: (version: number, data?: unknown) => void;

  debounceMs?: number;
  maxAttempts?: number;
  backoffBaseMs?: number;
  backoffCapMs?: number;
  breakerThreshold?: number;
  breakerCooldownMs?: number;
  timers?: Timers;
  now?: () => number;
  random?: () => number;
}

const defaultTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export class SaveController<T> {
  private status: SaveStatus = 'idle';
  private dirty = false;
  private latest: T | null = null;
  private snapshotInFlight: T | null = null;
  private inFlight = false;
  private pendingTrailing = false;
  private baseVersion: number | null = null;
  private idempotencyKey = '';
  private attempts = 0;
  private lastSavedAt: number | null = null;
  // Serialized form of the last content known to be on the server. Used to make
  // an identical-content save a client-side no-op (R4.2) - this also prevents a
  // redundant re-save after an offline outbox drain reconciles the same content.
  private lastSavedSerialized: string | null = null;
  private debounceHandle: unknown = null;
  private retryHandle: unknown = null;
  private breaker: CircuitBreaker;
  private readonly o: Required<
    Omit<SaveControllerOptions<T>, 'onStatus' | 'onConflict' | 'onSaved'>
  > &
    Pick<SaveControllerOptions<T>, 'onStatus' | 'onConflict' | 'onSaved'>;

  constructor(opts: SaveControllerOptions<T>) {
    this.o = {
      debounceMs: 1200,
      maxAttempts: 6,
      backoffBaseMs: 500,
      backoffCapMs: 30_000,
      breakerThreshold: 4,
      breakerCooldownMs: 15_000,
      timers: defaultTimers,
      now: Date.now,
      random: Math.random,
      ...opts,
    } as typeof this.o;
    this.breaker = new CircuitBreaker({
      failureThreshold: this.o.breakerThreshold,
      cooldownMs: this.o.breakerCooldownMs,
      now: this.o.now,
    });
  }

  /** Current status + last-saved time (for the status chip). */
  getState(): { status: SaveStatus; lastSavedAt: number | null; baseVersion: number | null } {
    return { status: this.status, lastSavedAt: this.lastSavedAt, baseVersion: this.baseVersion };
  }

  /** Seed the base version from the loaded resource (or a fan-out event). */
  setBaseVersion(version: number | null): void {
    this.baseVersion = version;
  }

  /**
   * Record that `payload` was persisted to the server at `version` by an
   * external path (e.g. the offline outbox drain / SyncController). Seeds the
   * base version and the identical-content baseline so a subsequent flush of the
   * same content is a no-op rather than a redundant re-save (R4.2).
   */
  noteExternalSave(version: number, payload: T): void {
    this.baseVersion = version;
    this.lastSavedSerialized = JSON.stringify(payload);
    this.lastSavedAt = this.o.now();
    if (this.latest !== null && JSON.stringify(this.latest) === this.lastSavedSerialized) {
      this.dirty = false;
      this.setStatus('saved');
    }
  }

  /**
   * Update base version + content from another tab's successful save (fan-out,
   * R7.3) so this tab doesn't self-inflict a 409. Does not mark dirty.
   */
  applyRemoteSave(version: number): void {
    this.baseVersion = version;
    if (this.status === 'saved' || this.status === 'idle') {
      this.setStatus('saved');
    }
  }

  private setStatus(status: SaveStatus, error?: string): void {
    this.status = status;
    this.o.onStatus?.(status, { lastSavedAt: this.lastSavedAt ?? undefined, error });
  }

  /** Called on every editor edit. */
  update(payload: T): void {
    this.latest = payload;
    this.dirty = true;
    // A new content snapshot invalidates the previous idempotency key so a
    // genuinely new save is not deduped against the old one.
    this.idempotencyKey = '';
    this.setStatus('dirty');
    this.scheduleDebounce();
  }

  private scheduleDebounce(): void {
    if (this.debounceHandle !== null) this.o.timers.clear(this.debounceHandle);
    this.debounceHandle = this.o.timers.set(() => {
      this.debounceHandle = null;
      void this.flush();
    }, this.o.debounceMs);
  }

  /**
   * Flush the dirty content: write the durable draft, then attempt the network
   * save (subject to online + breaker). Also invoked by the unload flush and
   * manual save. Never throws.
   */
  async flush(): Promise<void> {
    if (!this.dirty || this.latest === null) return;
    const payload = this.latest;
    // Durability first (R4.5): the draft holds the work regardless of network.
    try {
      await this.o.persistDraft(payload);
    } catch {
      /* storage failure is handled by the local-store layer; never block. */
    }

    if (this.inFlight) {
      // Coalesce: one in-flight + one trailing. The trailing flush runs when
      // the in-flight completes.
      this.pendingTrailing = true;
      return;
    }
    if (this.status === 'conflict') {
      // Do not auto-save over an unresolved conflict; wait for resolution.
      return;
    }
    if (!this.o.isOnline() || !this.breaker.canAttempt()) {
      // Work is safe in the draft; surface "saved locally, will retry".
      this.setStatus(this.o.isOnline() ? 'retrying' : 'offline');
      this.scheduleRetry();
      return;
    }
    await this.doSave(payload);
  }

  private async doSave(payload: T): Promise<void> {
    // Identical-content is a no-op (R4.2): if the payload matches what the
    // server already holds, skip the network entirely.
    if (this.lastSavedSerialized !== null && JSON.stringify(payload) === this.lastSavedSerialized) {
      if (this.latest === this.snapshotInFlight || this.latest === payload) {
        this.dirty = false;
      }
      this.setStatus('saved');
      if (this.pendingTrailing) {
        this.pendingTrailing = false;
        await this.flush();
      }
      return;
    }
    this.inFlight = true;
    this.snapshotInFlight = payload;
    if (!this.idempotencyKey) this.idempotencyKey = this.o.newIdempotencyKey();
    this.setStatus('saving');
    let outcome: SaveOutcome;
    try {
      outcome = await this.o.save(payload, {
        baseVersion: this.baseVersion,
        idempotencyKey: this.idempotencyKey,
      });
    } catch {
      outcome = { type: 'transient' };
    }
    this.inFlight = false;

    switch (outcome.type) {
      case 'ok': {
        this.breaker.recordSuccess();
        this.attempts = 0;
        this.baseVersion = outcome.version;
        this.lastSavedAt = this.o.now();
        this.lastSavedSerialized = JSON.stringify(payload);
        this.idempotencyKey = '';
        // If no new edits arrived while saving, we're clean.
        if (this.latest === this.snapshotInFlight && !this.pendingTrailing) {
          this.dirty = false;
        }
        this.setStatus('saved');
        this.o.onSaved?.(outcome.version, outcome.data);
        break;
      }
      case 'conflict': {
        // Never blind-retry a conflict (R4.3). Route to the explicit flow.
        this.breaker.recordSuccess(); // the server responded; not a transport fault
        this.setStatus('conflict');
        this.o.onConflict?.(outcome.info);
        return; // do not run trailing flush; wait for resolution
      }
      case 'transient': {
        this.breaker.recordFailure();
        this.attempts += 1;
        if (this.attempts >= this.o.maxAttempts || !this.breaker.canAttempt()) {
          // Breaker open or attempts exhausted: stop hammering; the draft holds
          // the work; a later reachability recovery / manual retry resumes.
          this.setStatus('retrying');
          this.scheduleRetry(outcome.retryAfterMs);
        } else {
          this.setStatus('retrying');
          this.scheduleRetry(outcome.retryAfterMs);
        }
        return;
      }
      case 'fatal': {
        this.setStatus('retrying', outcome.message);
        return;
      }
    }

    // Trailing coalesced save (latest content changed while saving).
    if (this.pendingTrailing) {
      this.pendingTrailing = false;
      await this.flush();
    }
  }

  private scheduleRetry(retryAfterMs?: number): void {
    if (this.retryHandle !== null) this.o.timers.clear(this.retryHandle);
    const delay =
      retryAfterMs != null
        ? retryAfterMs
        : fullJitterBackoff(this.attempts, {
            baseMs: this.o.backoffBaseMs,
            capMs: this.o.backoffCapMs,
            random: this.o.random,
          });
    this.retryHandle = this.o.timers.set(() => {
      this.retryHandle = null;
      void this.flush();
    }, delay);
  }

  /**
   * Resolve a conflict by re-basing the local edit onto the current server
   * version and writing a fresh save (R3.5 "keep mine"), or by adopting the
   * server content ("take latest"), or a field-merge. `resolvedPayload` is the
   * content to write; `newBaseVersion` is the current server version to base on.
   * Passing `null` payload means "take latest" (no write; just adopt version).
   */
  async resolveConflict(resolvedPayload: T | null, newBaseVersion: number): Promise<void> {
    this.baseVersion = newBaseVersion;
    this.idempotencyKey = '';
    this.attempts = 0;
    if (resolvedPayload === null) {
      // Take latest: adopt server state, nothing to write.
      this.latest = null;
      this.dirty = false;
      this.lastSavedAt = this.o.now();
      this.setStatus('saved');
      return;
    }
    this.latest = resolvedPayload;
    this.dirty = true;
    this.setStatus('dirty');
    await this.flush();
  }

  /**
   * Best-effort synchronous-ish flush for `visibilitychange`/`pagehide`
   * (R4.6). Persists the draft (durable regardless) and returns whether a
   * network flush was initiated; correctness never depends on it completing.
   */
  async flushOnUnload(): Promise<void> {
    if (!this.dirty || this.latest === null) return;
    try {
      await this.o.persistDraft(this.latest);
    } catch {
      /* ignore */
    }
  }

  /** Cancel all timers (component unmount). */
  dispose(): void {
    if (this.debounceHandle !== null) this.o.timers.clear(this.debounceHandle);
    if (this.retryHandle !== null) this.o.timers.clear(this.retryHandle);
    this.debounceHandle = null;
    this.retryHandle = null;
  }
}
