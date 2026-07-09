import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, apiPost, readCsrfToken, setUnauthorizedHandler } from '@/lib/api/client';

/**
 * Auth plumbing in the shared client (Task 8.2): credentials, CSRF injection on
 * mutations from the `csrf` cookie, and the single 401 interceptor (with the
 * `skipAuthHandling` opt-out). Network is stubbed.
 */

function clearCsrf() {
  document.cookie = 'csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
}

describe('api client — auth plumbing', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    clearCsrf();
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    clearCsrf();
    setUnauthorizedHandler(null);
  });

  it('always sends credentials so cookies ride along', async () => {
    await apiFetch('/health');
    expect(fetchMock.mock.calls[0][1].credentials).toBe('include');
  });

  it('injects X-CSRF-Token on mutations from the csrf cookie', async () => {
    document.cookie = 'csrf=tok-123; path=/';
    expect(readCsrfToken()).toBe('tok-123');
    await apiPost('/auth/logout', {});
    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers['X-CSRF-Token']).toBe('tok-123');
    expect(headers['Content-Type']).toBe('application/json');
  });

  it('does not add a CSRF header when no csrf cookie is present', async () => {
    await apiPost('/auth/logout', {});
    const headers = (fetchMock.mock.calls[0][1].headers as Record<string, string>) ?? {};
    expect(headers['X-CSRF-Token']).toBeUndefined();
  });

  it('does not add a CSRF header on safe (GET) requests', async () => {
    document.cookie = 'csrf=tok-123; path=/';
    await apiFetch('/auth/session');
    const headers = (fetchMock.mock.calls[0][1].headers as Record<string, string>) ?? {};
    expect(headers['X-CSRF-Token']).toBeUndefined();
  });

  it('invokes the unauthorized handler on a 401 for an app call', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }));
    await apiFetch('/resumes');
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('skips the unauthorized handler when skipAuthHandling is set', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }));
    await apiFetch('/auth/session', { skipAuthHandling: true });
    expect(handler).not.toHaveBeenCalled();
  });
});
