'use client';

/**
 * useRecovery — the coherent recovery surface (P4 R5.2, R5.3, R5.5).
 *
 * A read/manage view over the durable {@link ResilienceStore} for a user: lists
 * quarantined records (corrupt/undecryptable drafts, isolated so they never
 * poison the editor) and queued/failed outbox entries. Exposes non-destructive
 * actions — export (diagnostic download), discard, and retry-sync — so nothing
 * is ever dropped silently and the user is always in control.
 */
import * as React from 'react';
import {
  ResilienceStore,
  type OutboxEntry,
  type QuarantineRecord,
} from '@/lib/resilience/local-store';
import {
  IndexedDbEngine,
  MemoryEngine,
  indexedDbAvailable,
  type StoreEngine,
} from '@/lib/resilience/store-engine';

function defaultEngine(): StoreEngine {
  if (indexedDbAvailable()) {
    try {
      return new IndexedDbEngine();
    } catch {
      /* fall through */
    }
  }
  return new MemoryEngine();
}

export interface UseRecoveryResult {
  quarantine: QuarantineRecord[];
  outbox: OutboxEntry[];
  loading: boolean;
  refresh: () => Promise<void>;
  discardQuarantine: (id: string) => Promise<void>;
  exportQuarantine: (id: string) => void;
  discardOutbox: (id: string) => Promise<void>;
  hasAnything: boolean;
}

export function useRecovery(
  userId: string,
  opts: { engine?: StoreEngine; retrySync?: () => Promise<void> } = {}
): UseRecoveryResult {
  const [quarantine, setQuarantine] = React.useState<QuarantineRecord[]>([]);
  const [outbox, setOutbox] = React.useState<OutboxEntry[]>([]);
  const [loading, setLoading] = React.useState(true);
  const storeRef = React.useRef<ResilienceStore | null>(null);
  const injectedEngine = opts.engine;

  React.useEffect(() => {
    if (!userId) return;
    storeRef.current = new ResilienceStore(injectedEngine ?? defaultEngine(), userId);
    void refresh();
    return () => {
      storeRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, injectedEngine]);

  const refresh = React.useCallback(async () => {
    const store = storeRef.current;
    if (!store) return;
    setLoading(true);
    const [q, o] = await Promise.all([store.listQuarantine(), store.listOutbox()]);
    setQuarantine(q);
    setOutbox(o);
    setLoading(false);
  }, []);

  const discardQuarantine = React.useCallback(
    async (id: string) => {
      await storeRef.current?.discardQuarantine(id);
      await refresh();
    },
    [refresh]
  );

  const exportQuarantine = React.useCallback(
    (id: string) => {
      const rec = quarantine.find((r) => r.id === id);
      if (!rec || typeof window === 'undefined') return;
      // Diagnostic export: the raw isolated record (its payload may be
      // encrypted/corrupt — that's why it was quarantined). Lets the user keep a
      // copy / hand it to support before discarding, rather than losing it.
      const blob = new Blob([JSON.stringify(rec, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `fitwright-quarantine-${rec.id.replace(/[^\w.-]/g, '_')}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    [quarantine]
  );

  const discardOutbox = React.useCallback(
    async (id: string) => {
      await storeRef.current?.removeOutbox(id);
      await refresh();
    },
    [refresh]
  );

  return {
    quarantine,
    outbox,
    loading,
    refresh,
    discardQuarantine,
    exportQuarantine,
    discardOutbox,
    hasAnything: quarantine.length > 0 || outbox.length > 0,
  };
}
