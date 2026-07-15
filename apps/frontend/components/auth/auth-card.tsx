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

// Official multi-colour Google "G" mark (inline SVG so it renders in brand
// colours regardless of the button's text colour — `currentColor` would make it
// monochrome). Decorative: the button text ("Continue with Google") is the label.
const GoogleMark = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden focusable="false">
    <path
      fill="#4285F4"
      d="M23.52 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.47a5.53 5.53 0 0 1-2.4 3.63v3h3.87c2.26-2.09 3.58-5.17 3.58-8.87z"
    />
    <path
      fill="#34A853"
      d="M12 24c3.24 0 5.95-1.08 7.94-2.91l-3.87-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.28v3.09A11.997 11.997 0 0 0 12 24z"
    />
    <path
      fill="#FBBC05"
      d="M5.27 14.29a7.2 7.2 0 0 1 0-4.58V6.62H1.28a12 12 0 0 0 0 10.76l3.99-3.09z"
    />
    <path
      fill="#EA4335"
      d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.69 1.28 6.62l3.99 3.09C6.22 6.86 8.87 4.75 12 4.75z"
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
      // An unverified account that authenticates correctly is guided to the
      // verify/resend screen (email prefilled) rather than shown a dead-end
      // error — smooth recovery for "please verify your email before logging in".
      if (err instanceof AuthApiError && err.code === 'email_unverified') {
        router.replace(`/verify?email=${encodeURIComponent(email)}`);
        return;
      }
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
          href={`${isSignup ? '/login' : '/signup'}${
            next !== '/home' ? `?next=${encodeURIComponent(next)}` : ''
          }`}
          className="text-[var(--primary)] underline underline-offset-2"
        >
          {isSignup ? 'Sign in' : 'Sign up'}
        </Link>
      </p>
    </Card>
  );
}
