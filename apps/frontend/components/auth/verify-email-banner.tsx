'use client';

/**
 * Persistent "please verify your email" banner (Task 8.3 / R5.6, R15.4).
 * Shown across the authenticated app when the signed-in user's email is not yet
 * verified. Basic use is never blocked — only sensitive actions are gated
 * server-side — so this is a gentle prompt with an inline resend.
 */
import * as React from 'react';
import MailWarning from 'lucide-react/dist/esm/icons/mail-warning';
import { useSession } from '@/lib/context/session';
import { SINGLE_USER_MODE } from '@/lib/config/auth';
import { authApi } from '@/lib/api/auth';

export function VerifyEmailBanner() {
  const { user, status } = useSession();
  const [sent, setSent] = React.useState(false);
  const [pending, setPending] = React.useState(false);

  if (SINGLE_USER_MODE) return null;
  if (status !== 'authenticated' || !user || user.emailVerified) return null;

  async function onResend() {
    setPending(true);
    try {
      await authApi.requestVerification();
      setSent(true);
    } catch {
      /* uniform, non-leaky: silently ignore — the banner stays */
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] bg-[var(--at-ai)]/10 px-4 py-2 text-sm text-[var(--foreground)]"
    >
      <MailWarning className="h-4 w-4 shrink-0" />
      <span>Confirm your email to unlock everything.</span>
      {sent ? (
        <span className="text-[var(--muted-foreground)]">Link sent — check your inbox.</span>
      ) : (
        <button
          type="button"
          onClick={onResend}
          disabled={pending}
          className="font-medium text-[var(--primary)] underline-offset-2 hover:underline disabled:opacity-50"
        >
          {pending ? 'Sending…' : 'Resend link'}
        </button>
      )}
    </div>
  );
}
