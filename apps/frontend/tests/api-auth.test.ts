import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthApiError, fetchSession, login, signup, oauthStartUrl } from '@/lib/api/auth';

/**
 * The typed auth API client (Task 8.3): error-envelope parsing into
 * AuthApiError, the pre-session CSRF fetch before login, signup's
 * pending-verification branch, and the guest (401) session probe.
 */

const SAFE_USER = {
  id: 'u1',
  name: 'Ada',
  email: 'ada@example.com',
  role: 'user',
  status: 'active',
  emailVerified: true,
  aal: 'aal1',
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('auth api client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    document.cookie = 'csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetches a pre-session CSRF token before logging in', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/auth/csrf')) return Promise.resolve(jsonResponse({ csrfToken: 'x' }));
      if (url.endsWith('/auth/login')) return Promise.resolve(jsonResponse(SAFE_USER));
      throw new Error(`unexpected ${url}`);
    });
    const user = await login({ email: 'ada@example.com', password: 'pw' });
    expect(user.email).toBe('ada@example.com');
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls[0]).toContain('/auth/csrf');
    expect(urls[1]).toContain('/auth/login');
  });

  it('throws an AuthApiError carrying the backend code on login failure', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/auth/csrf')) return Promise.resolve(jsonResponse({ csrfToken: 'x' }));
      return Promise.resolve(
        jsonResponse(
          { error: { code: 'invalid_credentials', message: 'Invalid email or password.' } },
          401
        )
      );
    });
    await expect(login({ email: 'a@b.com', password: 'bad' })).rejects.toMatchObject({
      name: 'AuthApiError',
      code: 'invalid_credentials',
      status: 401,
    });
  });

  it('captures Retry-After on a rate_limited error', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/auth/csrf')) return Promise.resolve(jsonResponse({ csrfToken: 'x' }));
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'slow down' } }), {
          status: 429,
          headers: { 'Content-Type': 'application/json', 'Retry-After': '42' },
        })
      );
    });
    const err = await login({ email: 'a@b.com', password: 'x' }).catch((e) => e);
    expect(err).toBeInstanceOf(AuthApiError);
    expect((err as AuthApiError).retryAfter).toBe(42);
  });

  it('signup returns pendingVerification when verification is on', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/auth/csrf')) return Promise.resolve(jsonResponse({ csrfToken: 'x' }));
      return Promise.resolve(jsonResponse({ status: 'pending_verification' }));
    });
    const res = await signup({ email: 'a@b.com', password: 'longenoughpassword', name: 'A' });
    expect(res.pendingVerification).toBe(true);
    expect(res.user).toBeNull();
  });

  it('signup returns the user when verification is off', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/auth/csrf')) return Promise.resolve(jsonResponse({ csrfToken: 'x' }));
      return Promise.resolve(jsonResponse(SAFE_USER));
    });
    const res = await signup({
      email: 'ada@example.com',
      password: 'longenoughpassword',
      name: 'Ada',
    });
    expect(res.pendingVerification).toBe(false);
    expect(res.user?.email).toBe('ada@example.com');
  });

  it('fetchSession resolves to null for a guest (401)', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: 'unauthorized' } }, 401));
    await expect(fetchSession()).resolves.toBeNull();
  });

  it('fetchSession resolves to the user when authenticated', async () => {
    fetchMock.mockResolvedValue(jsonResponse(SAFE_USER));
    await expect(fetchSession()).resolves.toMatchObject({ id: 'u1' });
  });

  it('builds an OAuth start URL with a validated next', () => {
    expect(oauthStartUrl('google', '/home')).toContain('/auth/oauth/google/start?next=%2Fhome');
  });
});
