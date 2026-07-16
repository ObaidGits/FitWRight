/**
 * Server-side authoritative session resolution.
 *
 * Root + protected layouts both need the same answer. React `cache()` dedupes
 * those calls within one server render, avoiding the previous two-request SSR
 * waterfall through `/auth/session`.
 */
import { cache } from 'react';
import { cookies } from 'next/headers';
import { API_BASE } from './client';
import { SESSION_COOKIE_NAMES, SINGLE_USER_MODE } from '@/lib/config/auth';
import { OWNER_USER } from './session-owner';
import type { SafeUser } from './auth';

export { OWNER_USER };

/**
 * `resolved=true` distinguishes an authoritative guest (no cookie / backend
 * 401) from a transient auth-service failure. Previously both collapsed to
 * `null`, causing a redundant CSR probe on public pages and incorrect login
 * redirects when the session database was temporarily unavailable.
 */
export interface ServerSessionState {
  user: SafeUser | null;
  resolved: boolean;
}

export const getServerSession = cache(async (): Promise<ServerSessionState> => {
  if (SINGLE_USER_MODE) return { user: OWNER_USER, resolved: true };

  const cookieStore = await cookies();
  // A CSRF/theme/analytics cookie does not imply an authenticated session.
  // Only call the backend when one of the actual session cookie names exists;
  // otherwise a guest who previously visited /login incurs an unnecessary SSR
  // auth round-trip on every public page.
  const hasSessionCookie = SESSION_COOKIE_NAMES.some((name) => Boolean(cookieStore.get(name)));
  if (!hasSessionCookie) return { user: null, resolved: true };

  const cookieHeader = cookieStore.toString();

  try {
    const res = await fetch(`${API_BASE}/auth/session`, {
      headers: { cookie: cookieHeader },
      cache: 'no-store',
    });
    if (res.status === 401) return { user: null, resolved: true };
    if (!res.ok) return { user: null, resolved: false };
    return { user: (await res.json()) as SafeUser, resolved: true };
  } catch {
    return { user: null, resolved: false };
  }
});
