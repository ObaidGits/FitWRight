'use client';

/**
 * Draft persistence (Task 18 / Req 30.1, 30.2).
 *
 * Persists a serialisable working copy to localStorage so an accidental
 * reload, crash, or navigation never loses unsaved work. On mount it detects
 * a newer stored draft than the last save and exposes it for a RecoveryBanner
 * to offer (restore / discard) - restoration is always explicit, never
 * silent.
 */
import * as React from 'react';

const PREFIX = 'fitwright-draft:';

interface StoredDraft<T> {
  value: T;
  savedAt: number;
}

export interface DraftController<T> {
  /** A draft that was found on mount and is newer than the server copy. */
  recovered: T | null;
  recoveredAt: number | null;
  /** Persist the current working value (debounced by the caller if needed). */
  save: (value: T) => void;
  /** Remove the stored draft (after a successful server save or discard). */
  clear: () => void;
  /** Dismiss the recovery offer without clearing (user chose "keep editing"). */
  dismissRecovery: () => void;
}

export function useDraft<T>(key: string, enabled = true): DraftController<T> {
  const storageKey = PREFIX + key;
  const [recovered, setRecovered] = React.useState<T | null>(null);
  const [recoveredAt, setRecoveredAt] = React.useState<number | null>(null);

  // Detect an existing draft once, on mount.
  React.useEffect(() => {
    if (!enabled || !key) return;
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return;
      const parsed = JSON.parse(raw) as StoredDraft<T>;
      if (parsed && parsed.value != null) {
        setRecovered(parsed.value);
        setRecoveredAt(parsed.savedAt ?? null);
      }
    } catch {
      /* corrupt draft - ignore */
    }
  }, [storageKey, key, enabled]);

  const save = React.useCallback(
    (value: T) => {
      if (!enabled || !key) return;
      try {
        const payload: StoredDraft<T> = { value, savedAt: Date.now() };
        localStorage.setItem(storageKey, JSON.stringify(payload));
      } catch {
        /* storage full / unavailable - non-fatal */
      }
    },
    [storageKey, key, enabled]
  );

  const clear = React.useCallback(() => {
    try {
      localStorage.removeItem(storageKey);
    } catch {
      /* ignore */
    }
    setRecovered(null);
    setRecoveredAt(null);
  }, [storageKey]);

  const dismissRecovery = React.useCallback(() => {
    setRecovered(null);
    setRecoveredAt(null);
  }, []);

  return { recovered, recoveredAt, save, clear, dismissRecovery };
}
