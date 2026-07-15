'use client';

/**
 * Unsaved-changes guard — protects in-progress edits from every route
 * transition, with a clear Stay / Discard choice (never silent data loss).
 *
 * Coverage:
 * - **Reload / close tab / external navigation** → native `beforeunload` prompt.
 * - **In-app navigation** (Next `<Link>`, `<a>`, sidebar, bottom nav, in-page
 *   links) → capture-phase click interception shows the confirm dialog and, on
 *   Discard, performs the originally-intended navigation.
 * - **Browser Back/Forward** → `popstate` interception re-pins the current
 *   entry and confirms; Discard then performs the intended history move.
 *
 * Programmatic `router.push` cannot be globally intercepted in the App Router,
 * so call sites that navigate imperatively while edits may be pending should
 * gate on the same `when` flag (e.g. via {@link useUnsavedChangesGuardContext}).
 *
 * Accessibility: the dialog is the existing token-based `ConfirmDialog`
 * (focus-trapped, ESC-closable, labelled). Motion-safe.
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import { ConfirmDialog } from '@/components/atelier/confirm-dialog';

type PendingNav =
  | { kind: 'href'; href: string }
  | { kind: 'back' }
  | { kind: 'custom'; run: () => void };

interface GuardContextValue {
  /** Whether unsaved edits currently exist. */
  blocking: boolean;
  /**
   * Guard an imperative navigation (e.g. `router.push`). If edits are pending,
   * the confirm dialog is shown and `run` executes only on Discard; otherwise
   * `run` executes immediately.
   */
  guard: (run: () => void) => void;
}

const GuardContext = React.createContext<GuardContextValue | null>(null);

/** Access the guard from imperative navigation call sites (optional). */
export function useUnsavedChangesGuardContext(): GuardContextValue {
  return (
    React.useContext(GuardContext) ?? {
      blocking: false,
      guard: (run: () => void) => run(),
    }
  );
}

function isModifiedEvent(e: MouseEvent): boolean {
  return e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
}

export function UnsavedChangesGuard({
  when,
  title = 'Discard unsaved changes?',
  description = 'You have edits that haven’t been saved. If you leave now, they’ll be lost.',
  confirmLabel = 'Discard changes',
  cancelLabel = 'Stay',
  children,
}: {
  when: boolean;
  title?: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Optional: wrap children so imperative call sites can read the context. */
  children?: React.ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const pendingRef = React.useRef<PendingNav | null>(null);
  const bypassRef = React.useRef(false);
  const whenRef = React.useRef(when);
  React.useEffect(() => {
    whenRef.current = when;
  }, [when]);

  // --- Reload / close tab / external navigation -------------------------
  React.useEffect(() => {
    if (!when) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [when]);

  // --- In-app navigation via <a>/<Link> (capture phase) -----------------
  React.useEffect(() => {
    if (!when) return;
    const onClick = (e: MouseEvent) => {
      if (bypassRef.current || !whenRef.current) return;
      if (isModifiedEvent(e) || e.defaultPrevented) return;
      const anchor = (e.target as HTMLElement | null)?.closest('a');
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      const target = anchor.getAttribute('target');
      if (
        !href ||
        href.startsWith('#') ||
        href.startsWith('mailto:') ||
        href.startsWith('tel:') ||
        anchor.hasAttribute('download') ||
        (target && target !== '_self')
      )
        return;
      // Only guard same-origin in-app navigations.
      let url: URL;
      try {
        url = new URL(href, window.location.href);
      } catch {
        return;
      }
      if (url.origin !== window.location.origin) return;
      // Same URL → nothing to guard.
      if (url.pathname + url.search === window.location.pathname + window.location.search) return;

      e.preventDefault();
      e.stopPropagation();
      pendingRef.current = { kind: 'href', href: url.pathname + url.search + url.hash };
      setOpen(true);
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [when]);

  // --- Browser Back / Forward -------------------------------------------
  React.useEffect(() => {
    if (!when) return;
    // Seed a history entry so the first Back has something to intercept.
    window.history.pushState(null, '', window.location.href);
    const onPop = () => {
      if (bypassRef.current || !whenRef.current) return;
      // Re-pin the current entry (undo the Back the browser just performed),
      // then ask. On Discard we perform a single real Back.
      window.history.pushState(null, '', window.location.href);
      pendingRef.current = { kind: 'back' };
      setOpen(true);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [when]);

  const proceed = React.useCallback(() => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    setOpen(false);
    bypassRef.current = true;
    // Allow the guarded navigation to occur without re-triggering the guard.
    if (pending?.kind === 'href') {
      router.push(pending.href);
    } else if (pending?.kind === 'back') {
      window.history.back();
    } else if (pending?.kind === 'custom') {
      pending.run();
    }
    // Reset the bypass shortly after so subsequent edits are guarded again.
    window.setTimeout(() => {
      bypassRef.current = false;
    }, 400);
  }, [router]);

  const stay = React.useCallback(() => {
    pendingRef.current = null;
    setOpen(false);
  }, []);

  const guard = React.useCallback((run: () => void) => {
    if (!whenRef.current) {
      run();
      return;
    }
    pendingRef.current = { kind: 'custom', run };
    setOpen(true);
  }, []);

  const ctx = React.useMemo<GuardContextValue>(() => ({ blocking: when, guard }), [when, guard]);

  const dialog = (
    <ConfirmDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) stay();
      }}
      variant="warning"
      title={title}
      description={description}
      confirmLabel={confirmLabel}
      cancelLabel={cancelLabel}
      onConfirm={proceed}
      onCancel={stay}
      closeOnConfirm={false}
    />
  );

  if (children === undefined) return dialog;
  return (
    <GuardContext.Provider value={ctx}>
      {children}
      {dialog}
    </GuardContext.Provider>
  );
}
