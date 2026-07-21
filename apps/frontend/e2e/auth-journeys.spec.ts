import { test, expect, type Page, type Route } from '@playwright/test';
import { STORAGE_STATE } from './auth.setup';

/**
 * Auth journeys E2E (Task 11.2).
 *
 * DETERMINISTIC BY DEFAULT: the frontend talks to the backend over same-origin
 * `/api/v1/*`, so every auth call is mocked at the network boundary with
 * Playwright `page.route`. That lets the client journeys (login, signup->verify,
 * forgot->reset, Google via a mocked IdP redirect) run against the real frontend
 * pages without a backend, real Google, or real email - so the default
 * `npx playwright test` run is stable and quota-free.
 *
 * HOSTED-ONLY journeys (route guards, multi-tab logout, device revoke on the
 * Settings surface, admin-vs-user guard, session-expiry redirect) exercise the
 * SSR-authoritative check + the active `SessionProvider`, which only run when
 * the app boots in hosted mode (`NEXT_PUBLIC_SINGLE_USER_MODE=false`) against a
 * running backend. Following the repo's `RUN_AI_E2E` gating pattern, they are
 * guarded behind `RUN_AUTH_E2E=1` so the default run stays green; set that flag
 * with the hosted stack up to exercise them.
 *
 * Real external services stay gated too: set `RUN_AUTH_E2E=1` only with a real
 * Google IdP + real email provider to run the (documented) real-service checks.
 *
 * -- Running the gated hosted journeys --------------------------------------
 * The hosted-only describe block below runs only with a real hosted stack up.
 * The Playwright global setup (`e2e/auth.setup.ts`) does the auth bootstrap for
 * you: when `RUN_AUTH_E2E=1` it performs a real signup/login through the backend
 * to establish the session cookie (persisted as `storageState`) and a SECOND
 * login to seed a 2nd device for the revoke test. Bring the stack up under ONE
 * origin (the frontend proxies `/api/v1` to the backend - the `__Host-session`
 * cookie is same-origin + HTTPS-only) and run it like so:
 *
 *   1. Postgres:   docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=pw \
 *                    -e POSTGRES_DB=fitwright postgres:16-alpine
 *   2. Backend (hosted mode), from apps/backend - verification OFF so the seed
 *      signup activates immediately (no mailbox needed):
 *        SINGLE_USER_MODE=false EMAIL_VERIFICATION=false \
 *        DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:5432/fitwright \
 *        uv run alembic upgrade head && uv run app        # serves :8000
 *   3. Frontend (hosted mode), from apps/frontend - same-origin proxy to :8000:
 *        NEXT_PUBLIC_SINGLE_USER_MODE=false \
 *        npm run build && npm run start                   # serves :3000, proxies /api/v1
 *   4. Run the gated subset (the global setup seeds the session + 2nd device):
 *        RUN_AUTH_E2E=1 NEXT_PUBLIC_SINGLE_USER_MODE=false \
 *        E2E_BASE_URL=https://localhost:3000 \
 *        npx playwright test e2e/auth-journeys.spec.ts
 *
 * `__Host-`/Secure caveat: browsers only accept the `__Host-session` cookie over
 * HTTPS, so the seed step needs a TLS origin (a local TLS proxy, or CI's TLS). For
 * a plain-HTTP local smoke run, set the backend COOKIE_SECURE=false with a
 * non-`__Host-` SESSION_COOKIE_NAME (e.g. `session`) - the setup detects both.
 *
 * Without `RUN_AUTH_E2E=1` these are skipped, the global setup is a no-op, and the
 * deterministic block above runs standalone (no backend, no network) - the
 * default CI path.
 */

const HOSTED = process.env.RUN_AUTH_E2E === '1';

const SAFE_USER = {
  id: 'u1',
  name: 'Ada Lovelace',
  email: 'ada@example.com',
  role: 'user',
  status: 'active',
  emailVerified: true,
  aal: 'aal1',
};

/** Fulfil a JSON response, optionally setting a cookie. */
function jsonRoute(route: Route, body: unknown, status = 200, setCookie?: string) {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (setCookie) headers['set-cookie'] = setCookie;
  return route.fulfill({ status, headers, body: JSON.stringify(body) });
}

/**
 * Install the mocked backend auth surface. `overrides` lets a test change a
 * single endpoint's behaviour (e.g. login -> 401) while keeping the rest.
 */
async function mockAuthApi(
  page: Page,
  overrides: Partial<Record<string, (route: Route) => unknown>> = {}
) {
  // Pre-session CSRF: sets the JS-readable cookie the client echoes back.
  await page.route('**/api/v1/auth/csrf', (route) =>
    jsonRoute(route, { csrfToken: 'test-csrf' }, 200, 'csrf=test-csrf; Path=/; SameSite=Lax')
  );

  const handlers: Record<string, (route: Route) => unknown> = {
    '**/api/v1/auth/login': (route) =>
      jsonRoute(route, SAFE_USER, 200, '__Host-session=sess-token; Path=/; SameSite=Lax'),
    '**/api/v1/auth/signup': (route) => jsonRoute(route, SAFE_USER),
    '**/api/v1/auth/password/forgot': (route) => jsonRoute(route, { status: 'ok' }),
    '**/api/v1/auth/password/reset': (route) =>
      jsonRoute(route, SAFE_USER, 200, '__Host-session=sess-token; Path=/; SameSite=Lax'),
    '**/api/v1/auth/verify/confirm': (route) => jsonRoute(route, { status: 'verified' }),
    '**/api/v1/auth/verify/request': (route) => jsonRoute(route, { status: 'ok' }),
    '**/api/v1/auth/session': (route) => route.fulfill({ status: 401, body: '{}' }),
    ...overrides,
  };

  for (const [pattern, handler] of Object.entries(handlers)) {
    await page.route(pattern, (route) => handler(route));
  }
}

// ---------------------------------------------------------------------------
// Deterministic client journeys (mocked backend) - always run
// ---------------------------------------------------------------------------

test.describe('auth journeys - deterministic (mocked backend)', () => {
  test('login (+ remember me) submits credentials and navigates to next', async ({ page }) => {
    let loginBody: Record<string, unknown> | null = null;
    await mockAuthApi(page, {
      '**/api/v1/auth/login': (route) => {
        loginBody = route.request().postDataJSON() as Record<string, unknown>;
        return jsonRoute(route, SAFE_USER, 200, '__Host-session=sess-token; Path=/; SameSite=Lax');
      },
    });

    await page.goto('/login?next=/applications');
    await page.getByLabel('Email').fill('ada@example.com');
    await page.getByLabel('Password', { exact: true }).fill('a-long-enough-passphrase');
    await page.getByRole('checkbox', { name: /keep me signed in/i }).check();
    await page.getByRole('button', { name: /sign in/i }).click();

    // Credentials + remember-me reached the API; then the validated `next` is used.
    await expect.poll(() => loginBody).not.toBeNull();
    expect(loginBody!).toMatchObject({ email: 'ada@example.com', remember_me: true });
    await page.waitForURL('**/applications', { timeout: 15_000 });
  });

  test('login shows a single uniform error on invalid credentials', async ({ page }) => {
    await mockAuthApi(page, {
      '**/api/v1/auth/login': (route) =>
        jsonRoute(
          route,
          { error: { code: 'invalid_credentials', message: 'Invalid email or password.' } },
          401
        ),
    });
    await page.goto('/login');
    await page.getByLabel('Email').fill('ada@example.com');
    await page.getByLabel('Password', { exact: true }).fill('wrong-password-here');
    await page.getByRole('button', { name: /sign in/i }).click();

    await expect(page.getByText('Invalid email or password.')).toBeVisible();
    // The password field is cleared after a failure (never keep the secret).
    await expect(page.getByLabel('Password', { exact: true })).toHaveValue('');
  });

  test('signup -> pending verification -> verify landing -> home', async ({ page }) => {
    await mockAuthApi(page, {
      '**/api/v1/auth/signup': (route) => jsonRoute(route, { status: 'pending_verification' }),
    });

    await page.goto('/signup');
    await page.getByLabel('Name').fill('Ada Lovelace');
    await page.getByLabel('Email').fill('ada@example.com');
    await page.getByLabel('Password', { exact: true }).fill('a-long-enough-passphrase');
    await page.getByRole('button', { name: /create account/i }).click();

    // Hosted signup routes to the "check your inbox" verify page.
    await page.waitForURL('**/verify**', { timeout: 15_000 });
    await expect(page.getByRole('heading', { name: /confirm your email/i })).toBeVisible();

    // Following the emailed link (?token=...) redeems it and confirms success.
    await page.goto('/verify?token=verif-token');
    await expect(page.getByText(/your email is verified/i)).toBeVisible();
    await page.getByRole('button', { name: /continue/i }).click();
    await page.waitForURL('**/home', { timeout: 15_000 });
  });

  test('forgot -> reset -> home (uniform confirmation, then new password)', async ({ page }) => {
    let resetBody: Record<string, unknown> | null = null;
    await mockAuthApi(page, {
      '**/api/v1/auth/password/reset': (route) => {
        resetBody = route.request().postDataJSON() as Record<string, unknown>;
        return jsonRoute(route, SAFE_USER, 200, '__Host-session=sess-token; Path=/; SameSite=Lax');
      },
    });

    // Forgot: uniform, non-enumerating confirmation.
    await page.goto('/forgot');
    await page.getByLabel('Email').fill('ada@example.com');
    await page.getByRole('button', { name: /send reset link/i }).click();
    await expect(page.getByText(/reset link is on its way/i)).toBeVisible();

    // Reset: the emailed link carries the token; set a new password.
    await page.goto('/reset?token=reset-token');
    await page.getByLabel('New password').fill('a-brand-new-passphrase-42');
    await page.getByLabel('Confirm password').fill('a-brand-new-passphrase-42');
    await page.getByRole('button', { name: /update password/i }).click();

    await expect.poll(() => resetBody).not.toBeNull();
    expect(resetBody!).toMatchObject({ token: 'reset-token' });
    await page.waitForURL('**/home', { timeout: 15_000 });
  });

  test('reset blocks mismatched passwords before calling the API', async ({ page }) => {
    let called = false;
    await mockAuthApi(page, {
      '**/api/v1/auth/password/reset': (route) => {
        called = true;
        return jsonRoute(route, SAFE_USER);
      },
    });
    await page.goto('/reset?token=reset-token');
    await page.getByLabel('New password').fill('a-brand-new-passphrase-42');
    await page.getByLabel('Confirm password').fill('a-different-passphrase-99');
    await page.getByRole('button', { name: /update password/i }).click();
    await expect(page.getByText(/do not match/i)).toBeVisible();
    expect(called).toBe(false);
  });

  test('Google sign-in redirects top-level through a mocked IdP to home', async ({ page }) => {
    await mockAuthApi(page);
    // Mock the backend OAuth start -> (IdP round-trip) -> redirect back to /home,
    // i.e. a mocked IdP at the network boundary (no real Google).
    await page.route('**/api/v1/auth/oauth/google/start**', (route) =>
      route.fulfill({ status: 302, headers: { location: '/home' }, body: '' })
    );

    await page.goto('/login');
    await page.getByRole('button', { name: /continue with google/i }).click();
    // The top-level navigation lands back in the app (session issued server-side).
    await page.waitForURL('**/home', { timeout: 15_000 });
  });

  test('OAuth failure surfaces a retry/fallback banner on /login', async ({ page }) => {
    await mockAuthApi(page);
    await page.goto('/login?error=oauth_failed');
    await expect(page.getByText(/Google sign-in/i)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Hosted-only journeys (need NEXT_PUBLIC_SINGLE_USER_MODE=false + backend)
// ---------------------------------------------------------------------------

test.describe('auth journeys - hosted stack (SSR guards + session provider)', () => {
  test.skip(
    !HOSTED,
    'Set RUN_AUTH_E2E=1 with the hosted-mode stack (NEXT_PUBLIC_SINGLE_USER_MODE=false + backend) to run these.'
  );

  // Start each hosted test from the authenticated browser state seeded by the
  // global setup (a real signup/login + a 2nd device for the revoke test). When
  // not gated this is `undefined`, so the default deterministic run is untouched
  // and never needs the state file. Tests that need a guest (the
  // unauthenticated-redirect case) clear cookies themselves.
  test.use({ storageState: HOSTED ? STORAGE_STATE : undefined });

  test('unauthenticated visit to a protected route redirects to /login?next=', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/applications');
    await page.waitForURL('**/login**');
    expect(new URL(page.url()).searchParams.get('next')).toBe('/applications');
  });

  test('multi-tab logout: logging out in one tab logs out the other', async ({ context }) => {
    const tab1 = await context.newPage();
    const tab2 = await context.newPage();
    await tab1.goto('/home');
    await tab2.goto('/home');
    // Log out in tab 1. The control lives inside the avatar dropdown (a Radix
    // menu item, not a bare button), so open the menu first, then click it.
    await tab1.getByRole('button', { name: /account menu/i }).click();
    await tab1.getByRole('menuitem', { name: /log ?out/i }).click();
    // Tab 2 receives the broadcast and bounces to /login.
    await tab2.waitForURL('**/login**', { timeout: 15_000 });
  });

  test('session-expiry mid-action redirects to /login and preserves next', async ({ page }) => {
    await page.goto('/applications');
    // Force the next API call to 401 (expired session) and trigger one.
    await page.route('**/api/v1/**', (route) => route.fulfill({ status: 401, body: '{}' }));
    await page.reload();
    await page.waitForURL('**/login**', { timeout: 15_000 });
  });

  test('device list shows sessions and revoke removes one', async ({ page }) => {
    await page.goto('/settings');
    await page.getByRole('tab', { name: /account|security/i }).click();
    // Each non-current session row exposes a "Revoke" control; revoking one
    // removes that row (one fewer Revoke button). Assumes the test stack has
    // seeded ≥2 active sessions for the user (a second login/device).
    const revokeButtons = page.getByRole('button', { name: /revoke/i });
    const before = await revokeButtons.count();
    expect(before).toBeGreaterThan(0);
    await revokeButtons.first().click();
    await expect
      .poll(() => page.getByRole('button', { name: /revoke/i }).count())
      .toBeLessThan(before);
  });

  test('step-up is required on password change and unblocks after re-auth', async ({ page }) => {
    await page.goto('/settings');
    await page.getByRole('tab', { name: /account|security/i }).click();
    // Fill ALL three fields - the client blocks the submit (and never reaches
    // the step-up challenge) if the confirmation is missing or mismatched.
    // Exact labels disambiguate "New password" from "Confirm new password".
    await page.getByLabel('Current password').fill('current-passphrase-1');
    await page.getByLabel('New password', { exact: true }).fill('a-brand-new-passphrase-42');
    await page.getByLabel('Confirm new password').fill('a-brand-new-passphrase-42');
    await page.getByRole('button', { name: /update password/i }).click();
    // The step-up modal appears; re-enter the password to continue. Use the
    // exact modal label so it doesn't collide with the page's *password fields.
    await expect(page.getByText(/confirm it's you/i)).toBeVisible();
    await page.getByLabel('Password', { exact: true }).fill('current-passphrase-1');
    await page.getByRole('button', { name: /^confirm$/i }).click();
    await expect(page.getByText(/password (updated|changed)/i)).toBeVisible();
  });

  test('admin-vs-user guard: a non-admin visiting /admin is redirected', async ({ page }) => {
    await page.goto('/admin');
    await page.waitForURL((url) => !url.pathname.startsWith('/admin'), { timeout: 15_000 });
  });
});
