'use client';

/**
 * OfflineIndicator (Task 18 / Req 30.5).
 * A non-blocking banner shown while the browser reports no network. Full
 * offline editing is future work; this simply keeps the user informed so they
 * understand why saves/AI actions may fail. Drafts persist locally regardless.
 */
import * as React from 'react';
import WifiOff from 'lucide-react/dist/esm/icons/wifi-off';

export function OfflineIndicator() {
  const [offline, setOffline] = React.useState(false);

  React.useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    update();
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, []);

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
