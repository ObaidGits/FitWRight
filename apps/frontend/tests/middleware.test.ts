import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

/**
 * Presence-guard middleware (Task 8.2). It is UX-only: redirect to
 * `/login?next=...` when the session cookie is absent on a protected route, with
 * `next` propagation. In SINGLE_USER_MODE it is a no-op.
 *
 * The config flag is read at module load from the env var, so each scenario
 * resets modules and sets the env before a dynamic import.
 */
async function loadMiddleware(singleUser: boolean) {
  vi.resetModules();
  if (singleUser) delete process.env.NEXT_PUBLIC_SINGLE_USER_MODE;
  else process.env.NEXT_PUBLIC_SINGLE_USER_MODE = 'false';
  return import('@/middleware');
}

function request(path: string, cookie?: string) {
  return new NextRequest(`http://localhost${path}`, {
    headers: cookie ? { cookie } : {},
  });
}

describe('route-guard middleware', () => {
  const original = process.env.NEXT_PUBLIC_SINGLE_USER_MODE;
  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_SINGLE_USER_MODE;
  });
  afterEach(() => {
    if (original === undefined) delete process.env.NEXT_PUBLIC_SINGLE_USER_MODE;
    else process.env.NEXT_PUBLIC_SINGLE_USER_MODE = original;
  });

  it('redirects to /login?next=... on a protected route with no session (hosted)', async () => {
    const { middleware } = await loadMiddleware(false);
    const res = middleware(request('/home'));
    const location = res.headers.get('location');
    expect(location).toBeTruthy();
    const url = new URL(location!);
    expect(url.pathname).toBe('/login');
    expect(url.searchParams.get('next')).toBe('/home');
  });

  it('propagates the full path + query in next', async () => {
    const { middleware } = await loadMiddleware(false);
    const res = middleware(request('/applications?status=open'));
    const url = new URL(res.headers.get('location')!);
    expect(url.searchParams.get('next')).toBe('/applications?status=open');
  });

  it('passes through when a session cookie is present (hosted)', async () => {
    const { middleware } = await loadMiddleware(false);
    const res = middleware(request('/home', '__Host-session=abc'));
    expect(res.headers.get('location')).toBeNull();
    expect(res.headers.get('x-middleware-next')).toBe('1');
  });

  it('does not guard non-protected routes', async () => {
    const { middleware } = await loadMiddleware(false);
    const res = middleware(request('/'));
    expect(res.headers.get('location')).toBeNull();
  });

  it('guards the advanced editor (/builder) like every other app route (hosted)', async () => {
    const { middleware } = await loadMiddleware(false);
    const res = middleware(request('/builder?id=abc'));
    const url = new URL(res.headers.get('location')!);
    expect(url.pathname).toBe('/login');
    expect(url.searchParams.get('next')).toBe('/builder?id=abc');
  });

  it('passes /builder through when a session cookie is present (hosted)', async () => {
    const { middleware } = await loadMiddleware(false);
    const res = middleware(request('/builder?id=abc', '__Host-session=abc'));
    expect(res.headers.get('location')).toBeNull();
    expect(res.headers.get('x-middleware-next')).toBe('1');
  });

  it('is a no-op in SINGLE_USER_MODE even without a cookie', async () => {
    const { middleware } = await loadMiddleware(true);
    const res = middleware(request('/home'));
    expect(res.headers.get('location')).toBeNull();
    expect(res.headers.get('x-middleware-next')).toBe('1');
  });
});
