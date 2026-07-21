/**
 * Service-worker registration + safe-update controller (P4 R2, R7.1, R9.8).
 *
 * - Registers `/sw.js` only when OFFLINE_SUPPORT is enabled.
 * - Detects a waiting (updated) SW and surfaces `onUpdateReady` so the app can
 *   prompt "Update available - reload" at a *safe* point (no destructive
 *   skipWaiting mid-edit).
 * - `applyUpdate()` posts SKIP_WAITING then reloads once the new SW takes over.
 * - Kill-switch: `unregisterAndClear()` removes the SW + caches when
 *   OFFLINE_SUPPORT=off, and `clearCaches()` runs on logout / different-user.
 */

export interface SwCacheStats {
  version: string;
  hitRatio: number;
  stats: Record<string, number>;
}

export interface SwController {
  applyUpdate: () => void;
  clearCaches: () => void;
  unregisterAndClear: () => Promise<void>;
  /** Query the active SW for cache hit/miss stats (P4 §Observability). */
  getCacheStats: () => Promise<SwCacheStats | null>;
}

export interface RegisterOptions {
  onUpdateReady?: () => void;
  onControllerChange?: () => void;
}

function supported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
}

export async function registerServiceWorker(
  opts: RegisterOptions = {}
): Promise<SwController | null> {
  if (!supported()) return null;

  let waitingWorker: ServiceWorker | null = null;
  let reloading = false;

  const reg = await navigator.serviceWorker.register('/sw.js').catch(() => null);
  if (!reg) return null;

  const checkWaiting = () => {
    if (reg.waiting && navigator.serviceWorker.controller) {
      waitingWorker = reg.waiting;
      opts.onUpdateReady?.();
    }
  };
  checkWaiting();

  reg.addEventListener('updatefound', () => {
    const installing = reg.installing;
    if (!installing) return;
    installing.addEventListener('statechange', () => {
      if (installing.state === 'installed' && navigator.serviceWorker.controller) {
        // A new SW installed while an old one controls the page -> update ready,
        // but WAIT (no auto-activate) until the app reaches a safe point (R9.8).
        waitingWorker = reg.waiting;
        opts.onUpdateReady?.();
      }
    });
  });

  navigator.serviceWorker.addEventListener('controllerchange', () => {
    opts.onControllerChange?.();
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });

  const postToActive = (message: unknown) => {
    (reg.active || navigator.serviceWorker.controller)?.postMessage(message);
  };

  return {
    applyUpdate: () => {
      (waitingWorker || reg.waiting)?.postMessage({ type: 'SKIP_WAITING' });
    },
    clearCaches: () => {
      postToActive({ type: 'CLEAR_CACHES' });
    },
    unregisterAndClear: async () => {
      postToActive({ type: 'CLEAR_CACHES' });
      try {
        await reg.unregister();
      } catch {
        /* ignore */
      }
      if (typeof caches !== 'undefined') {
        const names = await caches.keys();
        await Promise.all(
          names.filter((n) => n.startsWith('fitwright-')).map((n) => caches.delete(n))
        );
      }
    },
    getCacheStats: () =>
      new Promise<SwCacheStats | null>((resolve) => {
        const target = reg.active || navigator.serviceWorker.controller;
        if (!target || typeof MessageChannel === 'undefined') {
          resolve(null);
          return;
        }
        const channel = new MessageChannel();
        const timer = setTimeout(() => resolve(null), 2000);
        channel.port1.onmessage = (ev) => {
          clearTimeout(timer);
          resolve(ev.data as SwCacheStats);
        };
        target.postMessage({ type: 'GET_STATS' }, [channel.port2]);
      }),
  };
}
