'use client';

/** Forgot-password request (Task 8.3 / R6.1, R15.4). Uniform, non-enumerating. */
import * as React from 'react';
import Link from 'next/link';
import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { ErrorBanner, InfoBanner, describeAuthError } from '@/components/auth/error-banner';
import { authApi } from '@/lib/api/auth';

export function ForgotCard() {
  const [email, setEmail] = React.useState('');
  const [pending, setPending] = React.useState(false);
  const [sent, setSent] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.includes('@')) {
      setError('Enter a valid email address.');
      return;
    }
    setPending(true);
    try {
      await authApi.forgotPassword(email);
      setSent(true);
    } catch (err) {
      setError(describeAuthError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold">Reset your password</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Enter your email and we&apos;ll send you a reset link.
        </p>
      </div>
      {sent ? (
        <InfoBanner message="If that email is registered, a reset link is on its way. Check your inbox." />
      ) : (
        <form onSubmit={onSubmit} className="space-y-3" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="forgot-email">Email</Label>
            <Input
              id="forgot-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </div>
          <ErrorBanner message={error} />
          <Button type="submit" className="w-full" loading={pending}>
            Send reset link
          </Button>
        </form>
      )}
      <p className="text-center text-sm text-[var(--muted-foreground)]">
        <Link href="/login" className="text-[var(--primary)] hover:underline">
          Back to sign in
        </Link>
      </p>
    </Card>
  );
}
