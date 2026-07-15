'use client';

/**
 * OfflineIndicator (Task 18 / Req 30.5).
 * A non-blocking banner shown while the browser reports no network. Full
 * offline editing is future work; this simply keeps the user informed so they
 * understand why saves/AI actions may fail. Drafts persist locally regardless.
 */
import * as React from 'react';
import WifiOff from 'lucide-react/dist/esm/icons/wifi-off';

/**
 * @param offline When provided (P4), reflects the real reachability probe
 * (source of truth, R2.6) rather than the advisory `navigator.onLine`. When
 * omitted it falls back to `navigator.onLine` for standalone use.
 */
export function OfflineIndicator({ offline: offlineProp }: { offline?: boolean } = {}) {
  const [navOffline, setNavOffline] = React.useState(false);

  React.useEffect(() => {
    if (offlineProp !== undefined) return; // parent controls it via the probe
    const update = () => setNavOffline(!navigator.onLine);
    update();
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, [offlineProp]);

  const offline = offlineProp ?? navOffline;
  if (!offline) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center gap-2 bg-[var(--at-warning)]/15 px-4 py-1.5 text-xs font-medium text-[var(--at-warning)]"
    >
      <WifiOff className="h-3.5 w-3.5" />
      You&apos;re offline. Changes are saved locally and will sync when you reconnect.
    </div>
  );
}
