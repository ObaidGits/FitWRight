'use client';

/** Set a new password with a reset token (Task 8.3 / R6.2, R6.3, R15.4). */
import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Label } from '@/components/atelier/label';
import { PasswordField } from '@/components/auth/password-field';
import { ErrorBanner, describeAuthError } from '@/components/auth/error-banner';
import { authApi } from '@/lib/api/auth';
import { useSession } from '@/lib/context/session';

export function ResetCard() {
  const router = useRouter();
  const params = useSearchParams();
  const { refresh } = useSession();
  const token = params.get('token') ?? '';

  const [password, setPassword] = React.useState('');
  const [confirm, setConfirm] = React.useState('');
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError('This reset link is invalid or has expired. Request a new one.');
      return;
    }
    if (password.length < 12) {
      setError('Password must be at least 12 characters.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setPending(true);
    try {
      await authApi.resetPassword({ token, password });
      await refresh();
      router.replace('/home');
    } catch (err) {
      setPassword('');
      setConfirm('');
      setError(describeAuthError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold">Choose a new password</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Enter a new password for your account.
        </p>
      </div>
      <form onSubmit={onSubmit} className="space-y-3" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="new-password">New password</Label>
          <PasswordField
            id="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            At least 12 characters. A passphrase works great.
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm-password">Confirm password</Label>
          <PasswordField
            id="confirm-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        <ErrorBanner message={error} />
        <Button type="submit" className="w-full" loading={pending}>
          Update password
        </Button>
      </form>
      <p className="text-center text-sm text-[var(--muted-foreground)]">
        <Link href="/forgot" className="text-[var(--primary)] hover:underline">
          Request a new link
        </Link>
      </p>
    </Card>
  );
}
