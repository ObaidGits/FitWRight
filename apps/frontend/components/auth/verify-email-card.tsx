'use client';

/**
 * Email verification (Task 8.3 / R5.*, R15.4).
 * - Landing (`?token=…`): redeem the token, then confirm success/failure.
 * - Pending (`?email=…`): a "check your inbox" banner + resend (rate-limited,
 *   uniform response).
 */
import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { ErrorBanner, InfoBanner, describeAuthError } from '@/components/auth/error-banner';
import { authApi } from '@/lib/api/auth';
import { useSession } from '@/lib/context/session';

export function VerifyEmailCard() {
  const params = useSearchParams();
  const token = params.get('token');
  if (token) return <VerifyLanding token={token} />;
  return (
    <VerifyPending initialEmail={params.get('email') ?? ''} sent={params.get('sent') === '1'} />
  );
}

function VerifyLanding({ token }: { token: string }) {
  const router = useRouter();
  const { refresh } = useSession();
  const [state, setState] = React.useState<'confirming' | 'ok' | 'error'>('confirming');
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    (async () => {
      try {
        await authApi.confirmVerification(token);
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
      <h1 className="text-xl font-semibold">Verify your email</h1>
      {state === 'confirming' && (
        <p className="text-sm text-[var(--muted-foreground)]">Confirming your email…</p>
      )}
      {state === 'ok' && (
        <>
          <InfoBanner message="Your email is verified. You're all set." />
          <Button className="w-full" onClick={() => router.replace('/home')}>
            Continue
          </Button>
        </>
      )}
      {state === 'error' && (
        <>
          <ErrorBanner message={error} />
          <Link href="/verify" className="text-sm text-[var(--primary)] hover:underline">
            Request a new link
          </Link>
        </>
      )}
    </Card>
  );
}

function VerifyPending({ initialEmail, sent }: { initialEmail: string; sent: boolean }) {
  const { user } = useSession();
  const [email, setEmail] = React.useState(initialEmail || user?.email || '');
  const [pending, setPending] = React.useState(false);
  const [info, setInfo] = React.useState<string | null>(
    sent ? 'Check your inbox for a confirmation link.' : null
  );
  const [error, setError] = React.useState<string | null>(null);

  async function onResend(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setPending(true);
    try {
      await authApi.requestVerification(email || undefined);
      // Uniform response — never discloses whether the address is registered.
      setInfo('If that email needs verifying, a new link is on its way.');
    } catch (err) {
      setError(describeAuthError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold">Confirm your email</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          We sent a confirmation link to your inbox. Didn&apos;t get it? Resend below.
        </p>
      </div>
      <form onSubmit={onResend} className="space-y-3" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="verify-email">Email</Label>
          <Input
            id="verify-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
        </div>
        <InfoBanner message={info} />
        <ErrorBanner message={error} />
        <Button type="submit" className="w-full" loading={pending}>
          Resend confirmation
        </Button>
      </form>
      <p className="text-center text-sm text-[var(--muted-foreground)]">
        <Link href="/login" className="text-[var(--primary)] hover:underline">
          Back to sign in
        </Link>
      </p>
    </Card>
  );
}
