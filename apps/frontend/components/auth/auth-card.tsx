'use client';

/**
 * Shared login/signup card (Task 8.3) — wired to the real auth API.
 *
 * Features: inline validation, a single non-leaky error banner, disabled-submit
 * while pending, password-manager `autocomplete`, password reveal + caps-lock
 * hint, a validated `next` redirect, a "Continue with Google" button, and the
 * OAuth-failure banner (`?error=oauth_failed`). On success the session is
 * re-hydrated and the user is routed to a validated same-origin `next`.
 */
import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { PasswordField } from '@/components/auth/password-field';
import { ErrorBanner, describeAuthError } from '@/components/auth/error-banner';
import { authApi, AuthApiError } from '@/lib/api/auth';
import { useSession } from '@/lib/context/session';

const GoogleMark = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden>
    <path
      fill="currentColor"
      d="M12 11v2.8h4a3.6 3.6 0 0 1-1.5 2.3l2.4 1.9A6 6 0 0 0 18 12c0-.4 0-.7-.1-1z"
    />
    <path
      fill="currentColor"
      d="M12 6.5a5.5 5.5 0 0 1 3.6 1.3l1.9-1.9A8 8 0 1 0 20 12h-8"
      opacity=".55"
    />
  </svg>
);

/** Open-redirect guard: only honor a same-origin path starting with a single `/`. */
export function safeNext(next: string | null | undefined): string {
  if (!next) return '/home';
  if (!next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return '/home';
  return next;
}

export function AuthCard({ mode }: { mode: 'login' | 'signup' }) {
  const isSignup = mode === 'signup';
  const router = useRouter();
  const params = useSearchParams();
  const { refresh } = useSession();

  const next = safeNext(params.get('next'));
  const oauthError = params.get('error') === 'oauth_failed';

  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [name, setName] = React.useState('');
  const [rememberMe, setRememberMe] = React.useState(false);
  const [error, setError] = React.useState<string | null>(
    oauthError ? describeAuthError(new AuthApiError('oauth_failed', '', 400)) : null
  );
  const [pending, setPending] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.includes('@')) {
      setError('Enter a valid email address.');
      return;
    }
    if (isSignup && !name.trim()) {
      setError('Enter your name.');
      return;
    }
    if (isSignup && password.length < 12) {
      setError('Password must be at least 12 characters.');
      return;
    }
    if (!password) {
      setError('Enter your password.');
      return;
    }

    setPending(true);
    try {
      if (isSignup) {
        const res = await authApi.signup({ email, password, name });
        if (res.pendingVerification) {
          router.replace(`/verify?email=${encodeURIComponent(email)}&sent=1`);
          return;
        }
        await refresh();
        router.replace(next);
      } else {
        await authApi.login({ email, password, rememberMe });
        await refresh();
        router.replace(next);
      }
    } catch (err) {
      setPassword(''); // never keep a secret around after a failure (R15.2)
      setError(describeAuthError(err));
    } finally {
      setPending(false);
    }
  }

  function onGoogle() {
    // Full-page navigation — the backend runs the IdP round-trip and issues the
    // session cookie server-side, then redirects back to `next` or /home.
    window.location.href = authApi.oauthStartUrl('google', next);
  }

  return (
    <Card className="p-6">
      <div className="mb-6 text-center">
        <h1 className="text-xl font-semibold">
          {isSignup ? 'Create your account' : 'Welcome back'}
        </h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          {isSignup ? 'Start tailoring resumes with FitWright.' : 'Sign in to continue.'}
        </p>
      </div>

      <Button type="button" variant="outline" className="w-full" onClick={onGoogle}>
        <GoogleMark /> Continue with Google
      </Button>

      <div className="my-4 flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
        <span className="h-px flex-1 bg-[var(--border)]" /> or{' '}
        <span className="h-px flex-1 bg-[var(--border)]" />
      </div>

      <form onSubmit={onSubmit} className="space-y-3" noValidate>
        {isSignup && (
          <div className="space-y-1.5">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            aria-invalid={!!error && !email.includes('@')}
          />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            {!isSignup && (
              <Link href="/forgot" className="text-xs text-[var(--primary)] hover:underline">
                Forgot password?
              </Link>
            )}
          </div>
          <PasswordField
            id="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isSignup ? 'new-password' : 'current-password'}
          />
          {isSignup && (
            <p className="text-xs text-[var(--muted-foreground)]">
              At least 12 characters. A passphrase works great.
            </p>
          )}
        </div>

        {!isSignup && (
          <label className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
              className="h-4 w-4 rounded border-[var(--border)]"
            />
            Keep me signed in
          </label>
        )}

        <ErrorBanner message={error} />

        <Button type="submit" className="w-full" loading={pending}>
          {isSignup ? 'Create account' : 'Sign in'}
        </Button>
      </form>

      <p className="mt-5 text-center text-sm text-[var(--muted-foreground)]">
        {isSignup ? 'Already have an account? ' : "Don't have an account? "}
        <Link
          href={isSignup ? '/login' : '/signup'}
          className="text-[var(--primary)] hover:underline"
        >
          {isSignup ? 'Sign in' : 'Sign up'}
        </Link>
      </p>
    </Card>
  );
}
