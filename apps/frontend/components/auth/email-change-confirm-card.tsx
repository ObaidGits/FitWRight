'use client';

/**
 * Email-change confirmation landing (Task 8.3 / R7.4).
 * The link is delivered to the NEW address; redeeming its token switches the
 * account's primary email (verify-before-switch). Token-only, single-use.
 */
import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { ErrorBanner, InfoBanner, describeAuthError } from '@/components/auth/error-banner';
import { authApi } from '@/lib/api/auth';
import { useSession } from '@/lib/context/session';

export function EmailChangeConfirmCard() {
  const router = useRouter();
  const params = useSearchParams();
  const { refresh } = useSession();
  const token = params.get('token');
  const [state, setState] = React.useState<'confirming' | 'ok' | 'error'>('confirming');
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!token) {
      setError('This confirmation link is invalid or has expired.');
      setState('error');
      return;
    }
    let active = true;
    (async () => {
      try {
        await authApi.confirmEmailChange(token);
        if (!active) return;
        setState('ok');
        await refresh();
      } catch (err) {
        if (!active) return;
        setError(describeAuthError(err));
        setState('error');
      }
    })();
    return () => {
      active = false;
    };
  }, [token, refresh]);

  return (
    <Card className="space-y-4 p-6 text-center">
      <h1 className="text-xl font-semibold">Confirm email change</h1>
      {state === 'confirming' && (
        <p className="text-sm text-[var(--muted-foreground)]">Confirming your new email...</p>
      )}
      {state === 'ok' && (
        <>
          <InfoBanner message="Your email address has been updated." />
          <Button className="w-full" onClick={() => router.replace('/settings')}>
            Back to settings
          </Button>
        </>
      )}
      {state === 'error' && (
        <>
          <ErrorBanner message={error} />
          <Link href="/settings" className="text-sm text-[var(--primary)] hover:underline">
            Back to settings
          </Link>
        </>
      )}
    </Card>
  );
}
