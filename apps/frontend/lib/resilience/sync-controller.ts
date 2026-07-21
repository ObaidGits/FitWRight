/**
 * SyncController - offline outbox replay (P4 R2.2, R5.2, Property 4).
 *
 * Replays queued edits **in FIFO order** through the version-CAS path when
 * connectivity returns. Guarantees:
 * - **Ordered**: entries replay oldest-first; a 409 on a resource *pauses that
 *   resource's* replay (raising the conflict flow) while other resources
 *   continue (R2.2, R3).
 * - **Idempotent**: each entry carries its idempotency key + base version, so a
 *   replay that already landed is deduped server-side (Property 4).
 * - **Storm-safe**: transient failures back off under a circuit breaker.
 * - **Never drops work**: a permanently-failed entry (attempts exhausted) is
 *   retained for the recovery surface (R5.2), never silently discarded.
 *
 * Transport-injected + pure, so it unit-tests without a browser. Runs
 * **leader-only** (the TabCoordinator gates who calls `sync()`).
 */

import { CircuitBreaker, fullJitterBackoff } from './backoff';
import type { OutboxEntry, ResilienceStore } from './local-store';
import type { ConflictInfo, Timers } from './save-controller';

export type SyncStatus = 'offline' | 'syncing' | 'synced' | 'conflict';

export type ReplayOutcome =
  | { type: 'ok'; version: number }
  | { type: 'conflict'; info: ConflictInfo }
  | { type: 'transient'; retryAfterMs?: number }
  | { type: 'fatal'; message?: string };

export interface SyncControllerOptions {
  store: Pick<
    ResilienceStore,
    'listOutbox' | 'openOutboxPayload' | 'removeOutbox' | 'recordOutboxAttempt'
  >;
  /** Replay one entry's decoded payload through the CAS PATCH path. */
  replay: (entry: OutboxEntry, payload: unknown) => Promise<ReplayOutcome>;
  isOnline: () => boolean;
  onStatus?: (status: SyncStatus, meta?: { pending: number }) => void;
  onConflict?: (entry: OutboxEntry, info: ConflictInfo) => void;
  maxAttempts?: number;
  backoffBaseMs?: number;
  backoffCapMs?: number;
  breakerThreshold?: number;
  breakerCooldownMs?: number;
  timers?: Timers;
  random?: () => number;
}

const defaultTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export class SyncController {
  private readonly o: Required<Omit<SyncControllerOptions, 'onStatus' | 'onConflict'>> &
    Pick<SyncControllerOptions, 'onStatus' | 'onConflict'>;
  private breaker: CircuitBreaker;
  private pausedResources = new Set<string>();
  private retryHandle: unknown = null;
  private running = false;
  private status: SyncStatus = 'synced';

  constructor(opts: SyncControllerOptions) {
    this.o = {
      maxAttempts: 8,
      backoffBaseMs: 500,
      backoffCapMs: 30_000,
      breakerThreshold: 4,
      breakerCooldownMs: 15_000,
      timers: defaultTimers,
      random: Math.random,
      ...opts,
    } as typeof this.o;
    this.breaker = new CircuitBreaker({
      failureThreshold: this.o.breakerThreshold,
      cooldownMs: this.o.breakerCooldownMs,
      now: Date.now,
    });
  }

  getStatus(): SyncStatus {
    return this.status;
  }

  private setStatus(status: SyncStatus, pending?: number): void {
    this.status = status;
    this.o.onStatus?.(status, pending != null ? { pending } : undefined);
  }

  /** Clear a resource's conflict pause after the user resolves it. */
  resumeResource(resumeId: string): void {
    this.pausedResources.delete(resumeId);
  }

  /**
   * Replay the outbox once (FIFO). Returns the number of entries still pending
   * (unsynced) after the pass. Never throws.
   */
  async syncOnce(): Promise<number> {
    if (this.running) return (await this.o.store.listOutbox()).length;
    this.running = true;
    try {
      if (!this.o.isOnline()) {
        const pending = (await this.o.store.listOutbox()).length;
        this.setStatus('offline', pending);
        return pending;
      }
      if (!this.breaker.canAttempt()) {
        const pending = (await this.o.store.listOutbox()).length;
        this.setStatus('syncing', pending);
        this.scheduleRetry();
        return pending;
      }

      const entries = await this.o.store.listOutbox();
      if (entries.length === 0) {
        this.setStatus('synced', 0);
        return 0;
      }
      this.setStatus('syncing', entries.length);

      let sawConflict = false;
      let sawTransient = false;

      for (const entry of entries) {
        if (this.pausedResources.has(entry.resumeId)) continue; // paused on conflict

        const opened = await this.o.store.openOutboxPayload<unknown>(entry);
        if (!opened.ok) {
          // Integrity failure: store already quarantined + removed the entry.
          continue;
        }

        let outcome: ReplayOutcome;
        try {
          outcome = await this.o.replay(entry, opened.payload);
        } catch {
          outcome = { type: 'transient' };
        }

        if (outcome.type === 'ok') {
          this.breaker.recordSuccess();
          await this.o.store.removeOutbox(entry.id);
        } else if (outcome.type === 'conflict') {
          this.breaker.recordSuccess(); // server responded; not a transport fault
          this.pausedResources.add(entry.resumeId);
          this.o.onConflict?.(entry, outcome.info);
          sawConflict = true;
          // Continue with other resources; this one is paused.
        } else if (outcome.type === 'transient') {
          this.breaker.recordFailure();
          await this.o.store.recordOutboxAttempt(
            entry.id,
            outcome.type === 'transient' ? 'transient' : null
          );
          sawTransient = true;
          // FIFO integrity: stop the pass so we never reorder this resource's
          // subsequent edits ahead of a not-yet-applied one.
          break;
        } else {
          // fatal: keep the entry for the recovery surface (never drop, R5.2).
          await this.o.store.recordOutboxAttempt(entry.id, outcome.message ?? 'fatal');
          sawTransient = true;
          break;
        }
      }

      const pending = (await this.o.store.listOutbox()).length;
      if (sawConflict && !sawTransient) {
        this.setStatus('conflict', pending);
      } else if (sawTransient) {
        this.setStatus('syncing', pending);
        this.scheduleRetry();
      } else if (pending === 0) {
        this.setStatus('synced', 0);
      } else if (this.pausedResources.size > 0) {
        this.setStatus('conflict', pending);
      } else {
        // More entries remain but none failed this pass (e.g. all paused).
        this.setStatus('synced', pending);
      }
      return pending;
    } finally {
      this.running = false;
    }
  }

  private scheduleRetry(retryAfterMs?: number): void {
    if (this.retryHandle !== null) this.o.timers.clear(this.retryHandle);
    const delay =
      retryAfterMs ??
      fullJitterBackoff(0, {
        baseMs: this.o.backoffBaseMs,
        capMs: this.o.backoffCapMs,
        random: this.o.random,
      });
    // The callback returns the syncOnce promise so an injected (test) timer can
    // await it deterministically; the real timer ignores the return value.
    const cb = (): Promise<number> => {
      this.retryHandle = null;
      return this.syncOnce();
    };
    this.retryHandle = this.o.timers.set(cb as () => void, delay);
  }

  dispose(): void {
    if (this.retryHandle !== null) this.o.timers.clear(this.retryHandle);
    this.retryHandle = null;
  }
}
