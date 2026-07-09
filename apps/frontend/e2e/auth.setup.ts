import { request, type APIRequestContext } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

/**
 * Playwright global setup for the gated hosted auth journeys (Task 11.2 harness).
 *
 * WHY THIS EXISTS: the hosted-only journeys in `auth-journeys.spec.ts` (route
 * guards, multi-tab logout, device revoke, session-expiry redirect, admin-vs-user
 * guard) assume a *pre-authenticated* browser — and the device-revoke test needs
 * ≥2 active sessions. Without a login step + persisted `storageState`, the first
 * protected navigation would bounce to `/login` and every hosted test would fail.
 *
 * WHAT IT DOES (only when `RUN_AUTH_E2E=1`): against the running hosted stack it
 * performs a *real* signup (falling back to login if the user already exists)
 * through the backend to establish the session cookie, then a **second** login
 * from a fresh cookie jar to seed a 2nd device/session for the revoke test, and
 * persists the first session via `storageState` to {@link STORAGE_STATE}. The
 * gated `describe` block wires that state in via `test.use({ storageState })`.
 *
 * NO-OP BY DEFAULT: with `RUN_AUTH_E2E` unset this returns immediately, so the
 * deterministic (mocked-backend) default run is completely unaffected — it never
 * touches the network and never writes a state file.
 *
 * ── Same-origin + cookie requirements ──────────────────────────────────────
 * The session cookie is `__Host-session`, which browsers ONLY accept over HTTPS
 * (the `__Host-` prefix mandates `Secure`). It must also be same-origin with the
 * app the browser loads. So the hosted test stack must serve the frontend and
 * proxy `/api/v1/*` to the backend under ONE HTTPS origin (that is how it runs in
 * CI/production — the Next.js rewrite forwards `/api/v1` to the backend). Point
 * `E2E_BASE_URL` at that origin (default `http://localhost:3000` for a plain-HTTP
 * dev proxy — see the note in `auth-journeys.spec.ts` about the `__Host-`/Secure
 * caveat on non-TLS local runs). `E2E_API_URL` may override the API origin if the
 * backend is reached directly rather than through the frontend proxy.
 */

/** Where the authenticated browser state is persisted for the gated suite. */
export const STORAGE_STATE = path.join(__dirname, '.auth', 'user.json');

/** Credentials for the seeded E2E user (override via env for a shared stack). */
const E2E_EMAIL = process.env.E2E_AUTH_EMAIL ?? 'e2e-auth@example.com';
const E2E_PASSWORD = process.env.E2E_AUTH_PASSWORD ?? 'a-long-enough-e2e-passphrase-42';
const E2E_NAME = process.env.E2E_AUTH_NAME ?? 'E2E Auth User';

/** The app origin the browser will load; the API rides the same origin (proxy). */
function baseUrl(): string {
  return process.env.E2E_BASE_URL ?? 'http://localhost:3000';
}

/** API origin — same as the app by default (Next proxies `/api/v1`). */
function apiBase(): string {
  const api = process.env.E2E_API_URL ?? baseUrl();
  return `${api.replace(/\/+$/, '')}/api/v1`;
}

/** Read the JS-readable CSRF token the backend set on the `csrf` cookie. */
async function csrfToken(ctx: APIRequestContext): Promise<string> {
  await ctx.get(`${apiBase()}/auth/csrf`);
  const state = await ctx.storageState();
  const csrf = state.cookies.find((c) => c.name === 'csrf');
  if (!csrf) {
    throw new Error(
      `[auth.setup] No 'csrf' cookie after GET /auth/csrf at ${apiBase()}. ` +
        'Is the hosted stack up and reachable at E2E_BASE_URL?'
    );
  }
  return csrf.value;
}

/**
 * Establish an authenticated session in `ctx`. Tries signup first (which signs in
 * immediately only when email verification is OFF — the test stack must set
 * `EMAIL_VERIFICATION=false`), and falls back to login when the user already
 * exists. Throws a clear, actionable error if neither path yields a session.
 */
async function authenticate(ctx: APIRequestContext): Promise<void> {
  // Signup (idempotent across runs): a fresh user is signed in immediately; an
  // existing email comes back 409 (verification off) and we fall through to login.
  const signup = await ctx.post(`${apiBase()}/auth/signup`, {
    headers: { 'X-CSRF-Token': await csrfToken(ctx) },
    data: { email: E2E_EMAIL, password: E2E_PASSWORD, name: E2E_NAME },
  });
  if (signup.ok()) {
    const body = await signup.json().catch(() => ({}));
    if (body?.status === 'pending_verification') {
      throw new Error(
        '[auth.setup] Signup returned pending_verification — the E2E stack has email ' +
          'verification ON, so there is no session and no mailbox to confirm it. Run the ' +
          'hosted test backend with EMAIL_VERIFICATION=false so signup activates immediately.'
      );
    }
    return; // signed in immediately (session cookie is now in ctx)
  }

  // Existing user (or verification-off duplicate) → log in for a fresh session.
  const login = await ctx.post(`${apiBase()}/auth/login`, {
    headers: { 'X-CSRF-Token': await csrfToken(ctx) },
    data: { email: E2E_EMAIL, password: E2E_PASSWORD, remember_me: true },
  });
  if (!login.ok()) {
    throw new Error(
      `[auth.setup] Could not authenticate the E2E user (signup ${signup.status()}, ` +
        `login ${login.status()}). Ensure the hosted backend is up with ` +
        'SINGLE_USER_MODE=false and EMAIL_VERIFICATION=false.'
    );
  }
}

export default async function globalSetup(): Promise<void> {
  // No-op unless the gated hosted suite is explicitly requested.
  if (process.env.RUN_AUTH_E2E !== '1') return;

  // 1) Primary session — persisted as the browser's storageState.
  const primary = await request.newContext({ baseURL: baseUrl() });
  await authenticate(primary);

  const state = await primary.storageState();
  const hasSession = state.cookies.some((c) => c.name === '__Host-session' || c.name === 'session');
  if (!hasSession) {
    await primary.dispose();
    throw new Error(
      "[auth.setup] Authenticated but no session cookie was persisted. The '__Host-' " +
        'session cookie requires an HTTPS same-origin stack; serve the hosted frontend + ' +
        'proxied backend over TLS (as in CI), or set the backend COOKIE_SECURE=false with a ' +
        'non-__Host- SESSION_COOKIE_NAME for a plain-HTTP local run.'
    );
  }

  await mkdir(path.dirname(STORAGE_STATE), { recursive: true });
  await writeFile(STORAGE_STATE, JSON.stringify(state, null, 2), 'utf8');
  await primary.dispose();

  // 2) Second device — a fresh cookie jar logs in again, creating a 2nd active
  //    server-side session so the device-revoke test has something to revoke.
  const secondDevice = await request.newContext({ baseURL: baseUrl() });
  await authenticate(secondDevice);
  await secondDevice.dispose();
}
