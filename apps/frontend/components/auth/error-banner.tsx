'use client';

/** Uniform, non-leaky auth error banner (Task 8.3 / R15.1). */
import * as React from 'react';
import { AuthApiError } from '@/lib/api/auth';

/**
 * Map an error to a single user-facing sentence. Backend messages are already
 * non-enumerating; we add friendly copy for the well-known codes and fall back
 * to the server message (then a generic line) so nothing leaks.
 */
export function describeAuthError(err: unknown): string {
  if (err instanceof AuthApiError) {
    switch (err.code) {
      case 'invalid_credentials':
        return 'Invalid email or password.';
      case 'email_unavailable':
        return 'That email is unavailable.';
      case 'account_disabled':
        return 'This account is disabled. Contact support if you think this is a mistake.';
      case 'email_unverified':
        return 'Please verify your email before logging in. Check your inbox for the confirmation link, or request a new one.';
      case 'rate_limited':
        return err.retryAfter
          ? `Too many attempts. Try again in about ${err.retryAfter}s.`
          : 'Too many attempts. Please try again in a little while.';
      case 'weak_password':
        return 'Choose a stronger password (at least 12 characters, not a common one).';
      case 'breached_password':
        return 'That password has appeared in a data breach. Please choose a different one.';
      case 'step_up_required':
        return 'Please re-enter your password to continue.';
      case 'invalid_token':
        return 'This link is invalid or has expired. Request a new one.';
      case 'csrf_failed':
        return 'Your session expired. Refresh the page and try again.';
      case 'oauth_failed':
        return "We couldn't complete Google sign-in. Try again, or sign in with your email and password and link Google from Settings.";
      default:
        return err.message || 'Something went wrong. Please try again.';
    }
  }
  if (err instanceof Error && err.message) return err.message;
  return 'Something went wrong. Please try again.';
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      aria-live="assertive"
      className="rounded-[var(--radius-at-md)] bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
    >
      {message}
    </p>
  );
}

export function InfoBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="status"
      aria-live="polite"
      className="rounded-[var(--radius-at-md)] bg-[var(--at-ai)]/10 px-3 py-2 text-sm text-[var(--foreground)]"
    >
      {message}
    </p>
  );
}
