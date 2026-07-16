import { beforeEach, describe, expect, it, vi } from 'vitest';

const cookiesMock = vi.fn();

vi.mock('next/headers', () => ({
  cookies: () => cookiesMock(),
}));
vi.mock('@/lib/config/auth', () => ({
  SINGLE_USER_MODE: false,
  SESSION_COOKIE_NAMES: ['__Host-session', 'session'],
}));
vi.mock('@/lib/api/client', () => ({ API_BASE: 'http://backend/api/v1' }));

function cookieStore(values: Record<string, string>) {
  return {
    get: (name: string) => (values[name] ? { name, value: values[name] } : undefined),
    toString: () =>
      Object.entries(values)
        .map(([k, v]) => `${k}=${v}`)
        .join('; '),
  };
}

describe('server session resolution', () => {
  beforeEach(() => {
    vi.resetModules();
    cookiesMock.mockReset();
    vi.stubGlobal('fetch', vi.fn());
  });

  it('resolves a CSRF-only guest without a backend round-trip', async () => {
    cookiesMock.mockResolvedValue(cookieStore({ csrf: 'guest-token' }));
    const { getServerSession } = await import('@/lib/api/session-server');
    await expect(getServerSession()).resolves.toEqual({ user: null, resolved: true });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('forwards cookies and resolves an authenticated session', async () => {
    const user = { id: 'u1', email: 'a@example.com' };
    cookiesMock.mockResolvedValue(cookieStore({ '__Host-session': 'secret', csrf: 'csrf-token' }));
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(user), { status: 200 }));
    const { getServerSession } = await import('@/lib/api/session-server');
    await expect(getServerSession()).resolves.toEqual({ user, resolved: true });
    expect(fetch).toHaveBeenCalledWith(
      'http://backend/api/v1/auth/session',
      expect.objectContaining({
        headers: { cookie: '__Host-session=secret; csrf=csrf-token' },
        cache: 'no-store',
      })
    );
  });

  it('distinguishes an authoritative 401 guest from a retryable backend outage', async () => {
    cookiesMock.mockResolvedValue(cookieStore({ session: 'token' }));
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 401 }));
    const first = await import('@/lib/api/session-server');
    await expect(first.getServerSession()).resolves.toEqual({ user: null, resolved: true });

    // React cache memoizes per imported function; reload the module to model a
    // separate server request for the outage case.
    vi.resetModules();
    cookiesMock.mockResolvedValue(cookieStore({ session: 'token' }));
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 503 }));
    const second = await import('@/lib/api/session-server');
    await expect(second.getServerSession()).resolves.toEqual({ user: null, resolved: false });
  });
});
