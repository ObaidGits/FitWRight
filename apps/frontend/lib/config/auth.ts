/**
 * Frontend auth configuration (Task 8.1).
 *
 * Mirrors the backend `SINGLE_USER_MODE` flag (see backend `app/config.py`). In
 * single-user/local mode the app boots with zero config, identical to today:
 * there is no login wall, the session is the bootstrap "owner" (admin), and the
 * presence-guard middleware is a no-op. Hosted deployments set
 * `NEXT_PUBLIC_SINGLE_USER_MODE=false`, which turns on the real session
 * hydration + route guards.
 *
 * SECURITY NOTE: this flag only changes *UX* (whether we bother hydrating a
 * session / redirecting). The server is always the access boundary - in hosted
 * mode every owned-resource endpoint enforces the authenticated `user_id`.
 */

/** True unless `NEXT_PUBLIC_SINGLE_USER_MODE` is explicitly `"false"`. */
export const SINGLE_USER_MODE: boolean =
  (process.env.NEXT_PUBLIC_SINGLE_USER_MODE ?? 'true').toLowerCase() !== 'false';

/**
 * Where a marketing "get started / enter the app" CTA should point, resolved by
 * deployment mode:
 * - single-user (local): straight into the workspace (`/home`) - there is no
 *   login wall, so no reason to detour a visitor through signup.
 * - hosted (multi-user): signup, the natural first step for a new visitor
 *   (going to `/home` would just bounce through the login guard).
 */
export const APP_ENTRY_HREF: string = SINGLE_USER_MODE ? '/home' : '/signup';

/** Name of the JS-readable CSRF cookie set by the backend (`csrf`). */
export const CSRF_COOKIE_NAME = 'csrf';

/**
 * Name of the httpOnly session cookie. `middleware.ts` only checks for its
 * *presence* (it can never read the value - httpOnly). The `__Host-` prefix is
 * used in hosted (HTTPS) mode; over plain HTTP the browser drops the prefix, so
 * we check both spellings for a robust presence probe.
 */
export const SESSION_COOKIE_NAMES = ['__Host-session', 'session'] as const;
