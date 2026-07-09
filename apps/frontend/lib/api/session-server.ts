/**
 * Server-side authoritative session check (Task 8.1).
 *
 * NOTE: this module is server-only by construction — it imports `next/headers`
 * (`cookies()`), which throws if ever pulled into a client bundle.
 *
 * The `(app)` and `admin` layouts call this per-request so the shell is
 * rendered with the real session state from the very first byte — no
 * unauthenticated flash, and no client round-trip before content shows.
 *
 * It reads the incoming request cookies (including the httpOnly
 * `__Host-session`, which the browser never exposes to JS) and forwards them to
 * the backend `GET /auth/session`. In `SINGLE_USER_MODE` there is no login wall,
 * so it returns the synthetic bootstrap owner — keeping local zero-config boot
 * identical to today (R14.3, R15.5).
 *
 * SECURITY NOTE: this is authoritative *for rendering*; the true access
 * boundary is always the backend enforcing `user_id` on every owned endpoint.
 */
import { cookies } from 'next/headers';
import { API_BASE } from './client';
import { SINGLE_USER_MODE } from '@/lib/config/auth';
import { OWNER_USER } from './session-owner';
import type { SafeUser } from './auth';

export { OWNER_USER };

/**
 * Resolve the session on the server. Returns the {@link SafeUser} when
 * authenticated (or the owner in single-user mode), otherwise `null`.
 */
export async function getServerSession(): Promise<SafeUser | null> {
  if (SINGLE_USER_MODE) return OWNER_USER;

  const cookieHeader = (await cookies()).toString();
  if (!cookieHeader) return null;

  try {
    const res = await fetch(`${API_BASE}/auth/session`, {
      headers: { cookie: cookieHeader },
      cache: 'no-store',
    });
    if (!res.ok) return null;
    return (await res.json()) as SafeUser;
  } catch {
    // Backend unreachable during SSR — treat as guest; the client will retry.
    return null;
  }
}
