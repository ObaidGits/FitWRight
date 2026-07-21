'use client';

/**
 * useAutosave - the durable editing linchpin (P4 R2, R3, R4, R5, R7, R8).
 *
 * Wires the pure controllers + durable store into the React editor:
 * - {@link SaveController} debounced server autosave (version CAS + idempotency
 *   + jittered-backoff retry + circuit breaker + durable-draft-first).
 * - {@link ResilienceStore} for the encrypted, integrity-checked local draft
 *   (crash safety net), the durable **outbox** op-log for offline edits, and
 *   quarantine of corrupt records.
 * - {@link SyncController} FIFO outbox replay via version CAS on reconnect.
 * - {@link TabCoordinator} so only the leader tab autosaves/flushes; save
 *   fan-out keeps follower tabs' base version fresh (no self-inflicted 409).
 * - {@link ReachabilityMonitor} so a real probe (not navigator.onLine) gates
 *   online-ness and triggers a drain+flush on reconnect.
 *
 * Sequencing (single source of truth at any instant): while the outbox is
 * non-empty the SyncController owns persistence (SaveController is gated off via
 * `isOnline`); once drained, the SaveController owns live autosave. This prevents
 * a double-write race between the two paths.
 */
import * as React from 'react';
import {
  SaveController,
  type ConflictInfo,
  type SaveStatus,
  type SaveOutcome,
} from '@/lib/resilience/save-controller';
import { SyncController, type ReplayOutcome } from '@/lib/resilience/sync-controller';
import { ResilienceStore, type OutboxEntry } from '@/lib/resilience/local-store';
import {
  IndexedDbEngine,
  MemoryEngine,
  indexedDbAvailable,
  type StoreEngine,
} from '@/lib/resilience/store-engine';
import { TabCoordinator } from '@/lib/resilience/tab-coordinator';
import { ReachabilityMonitor } from '@/lib/resilience/reachability';
import { ResumeConflictError, ResumeRequestError, updateResume } from '@/lib/api/resume';

function makeEngine(): { engine: StoreEngine; durable: boolean } {
  if (indexedDbAvailable()) {
    try {
      return { engine: new IndexedDbEngine(), durable: true };
    } catch {
      /* fall through */
    }
  }
  // Private mode / disabled storage: degrade to memory-only-with-warning (R8.4).
  return { engine: new MemoryEngine(), durable: false };
}

function newIdempotencyKey(): string {
  return typeof crypto?.randomUUID === 'function'
    ? crypto.randomUUID()
    : `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export interface UseAutosaveOptions {
  resumeId: string;
  userId: string;
  initialVersion: number | null;
  /** Autosave enabled (ADVANCED_AUTOSAVE flag). When false, only local drafts. */
  enabled?: boolean;
  /** Called when the server accepts a save with fresh server data. */
  onServerData?: (data: unknown, version: number) => void;
  debounceMs?: number;
  /**
   * Test seam: inject a storage engine (defaults to IndexedDB in the browser /
   * an in-memory engine in jsdom + private-mode). Lets integration tests drive
   * the full hook deterministically.
   */
  engineFactory?: () => { engine: StoreEngine; durable: boolean };
}

export interface RecoveryOffer<T> {
  payload: T;
  savedAt: number;
  baseVersion: number | null;
}

export interface UseAutosaveResult<T> {
  status: SaveStatus;
  lastSavedAt: number | null;
  isLeader: boolean;
  storageDegraded: boolean;
  conflict: ConflictInfo | null;
  /** The common-ancestor content for the conflict, for correct disjoint merge. */
  conflictBase: T | null;
  /** A recoverable local draft found on load (R5.1). */
  recovery: RecoveryOffer<T> | null;
  /** A corrupt draft was quarantined on load (R5.3). */
  quarantined: boolean;
  /** Count of queued offline edits awaiting replay (R2.5 UI). */
  pendingOutbox: number;
  update: (payload: T) => void;
  /** Seed the base version + synced content once the resource has loaded. */
  setBaseVersion: (version: number | null, content?: T) => void;
  flushNow: () => Promise<void>;
  /** Manually drain the outbox + flush (user-triggered retry from recovery UI). */
  retrySync: () => Promise<void>;
  resolveKeepMine: (payload: T) => Promise<void>;
  resolveTakeLatest: () => void;
  resolveMerge: (merged: T) => Promise<void>;
  acceptRecovery: () => void;
  dismissRecovery: () => void;
}

export function useAutosave<T extends Record<string, unknown>>(
  opts: UseAutosaveOptions
): UseAutosaveResult<T> {
  const {
    resumeId,
    userId,
    initialVersion,
    enabled = true,
    onServerData,
    debounceMs,
    engineFactory,
  } = opts;

  const [status, setStatus] = React.useState<SaveStatus>('idle');
  const [lastSavedAt, setLastSavedAt] = React.useState<number | null>(null);
  const [isLeader, setIsLeader] = React.useState(false);
  const [conflict, setConflict] = React.useState<ConflictInfo | null>(null);
  const [conflictBase, setConflictBase] = React.useState<T | null>(null);
  const [recovery, setRecovery] = React.useState<RecoveryOffer<T> | null>(null);
  const [quarantined, setQuarantined] = React.useState(false);
  const [storageDegraded, setStorageDegraded] = React.useState(false);
  const [pendingOutbox, setPendingOutbox] = React.useState(0);

  const storeRef = React.useRef<ResilienceStore | null>(null);
  const controllerRef = React.useRef<SaveController<T> | null>(null);
  const syncRef = React.useRef<SyncController | null>(null);
  const coordRef = React.useRef<TabCoordinator | null>(null);
  const reachRef = React.useRef<ReachabilityMonitor | null>(null);
  const latestRef = React.useRef<T | null>(null);
  const outboxCountRef = React.useRef(0);
  const drainRef = React.useRef<(() => Promise<void>) | null>(null);
  // Content at the current base version (the common ancestor for a conflict).
  const syncedContentRef = React.useRef<T | null>(null);

  // Build the durable store + controllers once per (user, resume).
  React.useEffect(() => {
    if (!userId || !resumeId) return;
    const { engine, durable } = (engineFactory ?? makeEngine)();
    const store = new ResilienceStore(engine, userId);
    storeRef.current = store;
    setStorageDegraded(!durable || store.isDegraded());

    const reach = new ReachabilityMonitor({ intervalMs: 20_000 });
    reachRef.current = reach;

    const coord = new TabCoordinator({
      userId,
      onLeadershipChange: (leader) => {
        setIsLeader(leader);
        // A newly-promoted leader immediately flushes any pending work (R7.2).
        if (leader) void drainThenFlush();
      },
      onRemoteSave: (rid, version) => {
        if (rid === resumeId) controllerRef.current?.applyRemoteSave(version);
      },
    });
    coordRef.current = coord;

    const refreshOutboxCount = async () => {
      const entries = await store.listOutbox();
      const mine = entries.filter((e) => e.resumeId === resumeId);
      outboxCountRef.current = mine.length;
      setPendingOutbox(mine.length);
    };

    // Coalesce offline edits for this resume into a single latest op-log entry
    // (each is a full snapshot; replaying the newest is sufficient and bounded).
    const queueOffline = async (payload: T, baseVersion: number | null) => {
      const existing = await store.listOutbox();
      for (const e of existing) {
        if (e.resumeId === resumeId) await store.removeOutbox(e.id);
      }
      await store.appendOutbox(resumeId, payload, baseVersion, newIdempotencyKey());
      await refreshOutboxCount();
    };

    const applyServerSave = (
      data: unknown,
      version: number,
      payload: T,
      opts: { reconcileController?: boolean } = {}
    ) => {
      onServerData?.(data, version);
      syncedContentRef.current = payload;
      // The SaveController's own save path already reconciles itself in doSave;
      // the SyncController (outbox drain) path did the write externally, so we
      // reconcile the controller here to make a subsequent flush a no-op (R4.2).
      if (opts.reconcileController) controllerRef.current?.noteExternalSave(version, payload);
      coord.broadcastSave(resumeId, version);
      void store.clearDraft(resumeId);
    };

    const save = async (
      payload: T,
      ctx: { baseVersion: number | null; idempotencyKey: string }
    ): Promise<SaveOutcome> => {
      if (!coord.isLeader()) return { type: 'transient' };
      try {
        const data = await updateResume(resumeId, payload, {
          baseVersion: ctx.baseVersion,
          idempotencyKey: ctx.idempotencyKey,
        });
        const version = data.version ?? (ctx.baseVersion ?? 0) + 1;
        applyServerSave(data, version, payload);
        return { type: 'ok', version, data };
      } catch (e) {
        if (e instanceof ResumeConflictError) {
          return {
            type: 'conflict',
            info: {
              yourBaseVersion: e.yourBaseVersion,
              currentVersion: e.currentVersion,
              currentData: e.currentData,
            },
          };
        }
        if (e instanceof ResumeRequestError) {
          if (e.status >= 400 && e.status < 500 && e.status !== 429) {
            return { type: 'fatal', message: e.message };
          }
          return { type: 'transient', retryAfterMs: e.retryAfterMs };
        }
        return { type: 'transient' };
      }
    };

    const controller = new SaveController<T>({
      save,
      persistDraft: async (payload) => {
        try {
          await store.saveDraft(resumeId, payload, controller.getState().baseVersion);
        } catch {
          setStorageDegraded(true);
        }
        // Offline: durably queue the edit to the outbox (R2.1). The SyncController
        // owns replay on reconnect; SaveController is gated off while pending.
        if (!reach.isReachable()) {
          try {
            await queueOffline(payload, controller.getState().baseVersion);
          } catch {
            setStorageDegraded(true);
          }
        }
      },
      // Live autosave only when reachable, leader, AND no queued offline work
      // (the SyncController drains the outbox first - single writer at a time).
      isOnline: () => reach.isReachable() && coord.isLeader() && outboxCountRef.current === 0,
      newIdempotencyKey,
      onStatus: (s, meta) => {
        setStatus(s);
        if (meta?.lastSavedAt) setLastSavedAt(meta.lastSavedAt);
      },
      onConflict: (info) => {
        setConflict(info);
        setConflictBase(syncedContentRef.current);
      },
      onSaved: () => setConflict(null),
      debounceMs,
    });
    controller.setBaseVersion(initialVersion);
    controllerRef.current = controller;

    // SyncController replays the outbox FIFO through the version-CAS path.
    const replay = async (entry: OutboxEntry, payload: unknown): Promise<ReplayOutcome> => {
      try {
        const data = await updateResume(resumeId, payload as Record<string, unknown>, {
          baseVersion: entry.baseVersion,
          idempotencyKey: entry.idempotencyKey,
        });
        const version = data.version ?? (entry.baseVersion ?? 0) + 1;
        applyServerSave(data, version, payload as T, { reconcileController: true });
        return { type: 'ok', version };
      } catch (e) {
        if (e instanceof ResumeConflictError) {
          return {
            type: 'conflict',
            info: {
              yourBaseVersion: e.yourBaseVersion,
              currentVersion: e.currentVersion,
              currentData: e.currentData,
            },
          };
        }
        if (e instanceof ResumeRequestError) {
          if (e.status >= 400 && e.status < 500 && e.status !== 429) {
            return { type: 'fatal', message: e.message };
          }
          return { type: 'transient', retryAfterMs: e.retryAfterMs };
        }
        return { type: 'transient' };
      }
    };

    const sync = new SyncController({
      store,
      replay,
      isOnline: () => reach.isReachable() && coord.isLeader(),
      onStatus: (s) => {
        // Surface sync status into the unified chip (offline/syncing/synced/conflict).
        if (s === 'syncing') setStatus('saving');
        else if (s === 'synced') void refreshOutboxCount();
        else if (s === 'offline') setStatus('offline');
        else if (s === 'conflict') setStatus('conflict');
      },
      onConflict: (_entry, info) => {
        setConflict(info);
        setConflictBase(syncedContentRef.current);
      },
    });
    syncRef.current = sync;

    // Drain the outbox (leader only), then flush any newer in-memory edit.
    const drainThenFlush = async () => {
      if (!coord.isLeader()) return;
      if (outboxCountRef.current > 0) {
        await sync.syncOnce();
        await refreshOutboxCount();
      }
      await controller.flush();
    };
    drainRef.current = drainThenFlush;

    coord.start();
    reach.start();
    void refreshOutboxCount();
    const unsub = reach.subscribe((reachable) => {
      if (reachable) void drainThenFlush();
    });

    // On-load reconcile: offer a newer/divergent local draft (R5.1) or flag a
    // quarantined (corrupt) record (R5.3).
    void store.loadDraft<T>(resumeId).then((load) => {
      if (load.status === 'ok') {
        setRecovery({
          payload: load.payload,
          savedAt: load.savedAt,
          baseVersion: load.baseVersion,
        });
      } else if (load.status === 'quarantined') {
        setQuarantined(true);
      }
    });

    // Best-effort unload flush (R4.6); the draft is already durable regardless.
    const onHide = () => {
      if (document.visibilityState === 'hidden') void controller.flushOnUnload();
    };
    const onPageHide = () => void controller.flushOnUnload();
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', onPageHide);

    return () => {
      unsub();
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', onPageHide);
      controller.dispose();
      sync.dispose();
      coord.dispose();
      reach.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, resumeId]);

  const update = React.useCallback(
    (payload: T) => {
      latestRef.current = payload;
      if (!enabled) {
        void storeRef.current?.saveDraft(
          resumeId,
          payload,
          controllerRef.current?.getState().baseVersion ?? null
        );
        return;
      }
      controllerRef.current?.update(payload);
    },
    [enabled, resumeId]
  );

  const setBaseVersion = React.useCallback((version: number | null, content?: T) => {
    controllerRef.current?.setBaseVersion(version);
    if (content !== undefined) syncedContentRef.current = content;
  }, []);

  const flushNow = React.useCallback(async () => {
    await controllerRef.current?.flush();
  }, []);

  const retrySync = React.useCallback(async () => {
    await reachRef.current?.check();
    await drainRef.current?.();
  }, []);

  const clearOutboxForResume = React.useCallback(async () => {
    const store = storeRef.current;
    if (!store) return;
    const entries = await store.listOutbox();
    for (const e of entries) if (e.resumeId === resumeId) await store.removeOutbox(e.id);
    outboxCountRef.current = 0;
    setPendingOutbox(0);
    syncRef.current?.resumeResource(resumeId);
  }, [resumeId]);

  const resolveKeepMine = React.useCallback(
    async (payload: T) => {
      const c = conflict;
      setConflict(null);
      setConflictBase(null);
      if (!c) return;
      // Re-base onto the server's current version and clear the stale queued op.
      await clearOutboxForResume();
      await controllerRef.current?.resolveConflict(payload, c.currentVersion);
    },
    [conflict, clearOutboxForResume]
  );

  const resolveTakeLatest = React.useCallback(() => {
    const c = conflict;
    setConflict(null);
    setConflictBase(null);
    if (!c) return;
    void clearOutboxForResume();
    void controllerRef.current?.resolveConflict(null, c.currentVersion);
    if (c.currentData) onServerData?.(c.currentData, c.currentVersion);
    syncedContentRef.current = (c.currentData as T) ?? syncedContentRef.current;
  }, [conflict, onServerData, clearOutboxForResume]);

  const resolveMerge = React.useCallback(
    async (merged: T) => {
      const c = conflict;
      setConflict(null);
      setConflictBase(null);
      if (!c) return;
      await clearOutboxForResume();
      await controllerRef.current?.resolveConflict(merged, c.currentVersion);
    },
    [conflict, clearOutboxForResume]
  );

  const acceptRecovery = React.useCallback(() => {
    setRecovery(null);
  }, []);

  const dismissRecovery = React.useCallback(() => {
    setRecovery(null);
    void storeRef.current?.clearDraft(resumeId);
  }, [resumeId]);

  return {
    status,
    lastSavedAt,
    isLeader,
    storageDegraded,
    conflict,
    conflictBase,
    recovery,
    quarantined,
    pendingOutbox,
    update,
    setBaseVersion,
    flushNow,
    retrySync,
    resolveKeepMine,
    resolveTakeLatest,
    resolveMerge,
    acceptRecovery,
    dismissRecovery,
  };
}
