'use client';

/**
 * Deployment-mode mismatch guard (fail-loud on misconfiguration).
 *
 * `NEXT_PUBLIC_SINGLE_USER_MODE` (frontend, baked at build time) and the
 * backend's `SINGLE_USER_MODE` are independent flags. When they disagree the
 * app is in a broken state - e.g. a hosted UI (login wall, real session
 * hydration) talking to a single-user backend that auto-owns everything, or a
 * single-user UI (synthetic owner, no login) talking to a hosted backend that
 * 401s every request. Both produce confusing "logged in but nothing works" /
 * "why am I asked to log in" symptoms.
 *
 * This surfaces the mismatch loudly: a console error for operators and a
 * dismissible in-app banner. It renders nothing when the two modes agree (the
 * normal case), so it is invisible in a correct deployment.
 */
import * as React from 'react';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import X from 'lucide-react/dist/esm/icons/x';
import { useSystemStatus } from '@/features/home/hooks';
import { SINGLE_USER_MODE } from '@/lib/config/auth';

export function ModeMismatchBanner() {
  const { data } = useSystemStatus();
  const backendSingleUser = data?.single_user;
  // Only decide once the backend has actually reported a boolean.
  const mismatch = typeof backendSingleUser === 'boolean' && backendSingleUser !== SINGLE_USER_MODE;
  const [dismissed, setDismissed] = React.useState(false);

  React.useEffect(() => {
    if (!mismatch) return;
    console.error(
      '[FitWright] Deployment mode mismatch: frontend NEXT_PUBLIC_SINGLE_USER_MODE=' +
        `${SINGLE_USER_MODE} but backend SINGLE_USER_MODE=${backendSingleUser}. ` +
        'Auth/session behavior will be inconsistent. Set both to the same value, ' +
        'and rebuild the frontend (NEXT_PUBLIC_* is inlined at build time).'
    );
  }, [mismatch, backendSingleUser]);

  if (!mismatch || dismissed) return null;

  return (
    <div
      role="alert"
      className="flex items-start gap-2 border-b border-[var(--at-warning)]/40 bg-[var(--at-warning)]/12 px-4 py-2 text-xs text-[var(--foreground)]"
    >
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--at-warning)]" aria-hidden />
      <p className="flex-1">
        <span className="font-medium">Configuration mismatch.</span> The interface is running in{' '}
        <span className="font-medium">{SINGLE_USER_MODE ? 'single-user' : 'hosted'}</span> mode but
        the backend is in{' '}
        <span className="font-medium">{backendSingleUser ? 'single-user' : 'hosted'}</span> mode.
        Sign-in and data may behave inconsistently. Align <code>NEXT_PUBLIC_SINGLE_USER_MODE</code>{' '}
        with the backend&apos;s <code>SINGLE_USER_MODE</code> and rebuild the frontend.
      </p>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => setDismissed(true)}
        className="shrink-0 rounded p-0.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
