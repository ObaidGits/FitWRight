'use client';

/**
 * ResilienceProvider (P4 R2, R6.4, R9.8, R9.12).
 *
 * App-level glue that:
 * - Registers the service worker when OFFLINE_SUPPORT is on; unregisters +
 *   clears caches when it's off (the ADR-14 kill-switch).
 * - Runs the reachability monitor and renders the DegradationBanner with the
 *   current named level (offline / degraded / safe-mode).
 * - Surfaces a safe "Update available - reload" prompt when a new SW is waiting;
 *   the user chooses the safe point (never a destructive mid-edit activation).
 * - Clears local data + SW caches on logout / different-user detection (R8.2/8.5).
 */
import * as React from 'react';
import { useSession } from '@/lib/context/session';
import { useResilienceFlags } from '@/lib/hooks/use-resilience-flags';
import { ReachabilityMonitor, probeApiVersion } from '@/lib/resilience/reachability';
import { indexedDbAvailable, IndexedDbEngine, MemoryEngine } from '@/lib/resilience/store-engine';
import { ResilienceStore } from '@/lib/resilience/local-store';
import { computeDegradation, type DegradationLevel } from '@/lib/resilience/degradation';
import { registerServiceWorker, type SwController } from '@/lib/resilience/sw-register';
import { DegradationBanner } from './degradation-banner';

export function ResilienceProvider({ children }: { children: React.ReactNode }) {
  const { user } = useSession();
  const { flags } = useResilienceFlags();
  const [reachable, setReachable] = React.useState(true);
  const [updateReady, setUpdateReady] = React.useState(false);
  const [safeMode, setSafeMode] = React.useState(false);
  const swRef = React.useRef<SwController | null>(null);
  const prevUserRef = React.useRef<string | null>(null);
  const baselineVersionRef = React.useRef<string | null>(null);

  // Reachability monitor (source of truth for online-ness, R2.6).
  React.useEffect(() => {
    const monitor = new ReachabilityMonitor({ intervalMs: 25_000 });
    const unsub = monitor.subscribe(setReachable);
    monitor.start();
    return () => {
      unsub();
      monitor.stop();
    };
  }, []);

  // API-version-skew detection (R9.8): pin the first-seen server version, then
  // flag Safe-Mode if a later probe reports a different version (deploy
  // mid-session). Safe-Mode blocks writes + prompts a reload at a safe point.
  React.useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const { reachable: ok, apiVersion } = await probeApiVersion();
      if (cancelled || !ok || !apiVersion) return;
      if (baselineVersionRef.current == null) {
        baselineVersionRef.current = apiVersion;
      } else if (baselineVersionRef.current !== apiVersion) {
        setSafeMode(true);
      }
    };
    void check();
    const handle = setInterval(() => void check(), 60_000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, []);

  // Service worker lifecycle, gated by the OFFLINE_SUPPORT flag.
  React.useEffect(() => {
    let cancelled = false;
    if (flags.offline_support) {
      registerServiceWorker({
        onUpdateReady: () => !cancelled && setUpdateReady(true),
      }).then((ctrl) => {
        if (!cancelled) swRef.current = ctrl;
      });
    } else {
      // Kill-switch: remove any previously-registered SW + caches cleanly.
      registerServiceWorker().then((ctrl) => void ctrl?.unregisterAndClear());
    }
    return () => {
      cancelled = true;
    };
  }, [flags.offline_support]);

  // Clear local data + caches on logout / different-user (R8.2/8.5).
  React.useEffect(() => {
    const current = user?.id ?? null;
    const prev = prevUserRef.current;
    if (prev && prev !== current) {
      const engine = indexedDbAvailable() ? new IndexedDbEngine() : new MemoryEngine();
      void new ResilienceStore(engine, prev).clearUser();
      swRef.current?.clearCaches();
    }
    prevUserRef.current = current;
  }, [user?.id]);

  const level: DegradationLevel = React.useMemo(
    () =>
      computeDegradation({
        backendReachable: reachable,
        aiAvailable: reachable,
        streamingAvailable: reachable && flags.streaming_ai,
        storageOk: indexedDbAvailable(),
        apiVersionSkew: safeMode,
      }),
    [reachable, flags.streaming_ai, safeMode]
  );

  const applyUpdate = React.useCallback(() => {
    swRef.current?.applyUpdate();
  }, []);

  return (
    <>
      <DegradationBanner level={level} onReload={applyUpdate} />
      {updateReady && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 bg-[var(--primary)]/10 px-4 py-2 text-xs font-medium text-[var(--foreground)]"
        >
          <span>A new version is available.</span>
          <button type="button" className="underline underline-offset-2" onClick={applyUpdate}>
            Reload to update
          </button>
        </div>
      )}
      {children}
    </>
  );
}
