import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AdminApiError,
  bulkDisable,
  deleteUser,
  getStats,
  getUsageSeries,
  listUsers,
  setUserRole,
  setUserStatus,
} from '@/lib/api/admin';
import { setUnauthorizedHandler } from '@/lib/api/client';

/**
 * Admin API client (Task 8.1) - request shaping (paths, query params, methods)
 * and error-envelope parsing into {@link AdminApiError}. Network is stubbed.
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('admin api client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(json({}));
    vi.stubGlobal('fetch', fetchMock);
    document.cookie = 'csrf=tok-1; path=/';
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.cookie = 'csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  const urlOf = (call = 0) => String(fetchMock.mock.calls[call][0]);
  const initOf = (call = 0) => fetchMock.mock.calls[call][1] as RequestInit;

  it('GET /admin/stats', async () => {
    fetchMock.mockResolvedValueOnce(json({ totalUsers: 3, computedAt: 't', stale: false }));
    const stats = await getStats();
    expect(urlOf()).toContain('/admin/stats');
    expect(stats.totalUsers).toBe(3);
  });

  it('usage-series encodes metric + window', async () => {
    fetchMock.mockResolvedValueOnce(
      json({ metric: 'signups', window: 7, points: [], computedAt: 't' })
    );
    await getUsageSeries('signups', 7);
    expect(urlOf()).toContain('metric=signups');
    expect(urlOf()).toContain('window=7');
  });

  it('listUsers only serializes provided filters (no empty params)', async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [], nextCursor: null }));
    await listUsers({ q: 'ab', status: 'active', deleted: true, limit: 25 });
    const url = urlOf();
    expect(url).toContain('q=ab');
    expect(url).toContain('status=active');
    expect(url).toContain('deleted=true');
    expect(url).not.toContain('role=');
    expect(url).not.toContain('cursor=');
  });

  it('setUserStatus routes to /disable and /enable', async () => {
    fetchMock.mockImplementation(async () => json({ changed: true }));
    await setUserStatus('u1', 'disabled');
    expect(urlOf()).toContain('/admin/users/u1/disable');
    expect(initOf().method).toBe('POST');
    await setUserStatus('u2', 'active');
    expect(urlOf(1)).toContain('/admin/users/u2/enable');
  });

  it('setUserRole PATCHes the role', async () => {
    fetchMock.mockResolvedValueOnce(json({ changed: true }));
    await setUserRole('u1', 'admin');
    expect(urlOf()).toContain('/admin/users/u1');
    expect(initOf().method).toBe('PATCH');
    expect(JSON.parse(String(initOf().body))).toEqual({ role: 'admin' });
  });

  it('deleteUser posts the typed email confirmation', async () => {
    fetchMock.mockResolvedValueOnce(json({ changed: true }));
    await deleteUser('u1', 'a@b.c');
    expect(urlOf()).toContain('/admin/users/u1/delete');
    expect(JSON.parse(String(initOf().body))).toEqual({ email: 'a@b.c' });
  });

  it('bulkDisable posts the id list', async () => {
    fetchMock.mockResolvedValueOnce(json({ results: [], disabled: 0, skipped: 0 }));
    await bulkDisable(['a', 'b']);
    expect(urlOf()).toContain('/admin/users/bulk-disable');
    expect(JSON.parse(String(initOf().body))).toEqual({ ids: ['a', 'b'] });
  });

  it('maps the error envelope into AdminApiError with the machine code', async () => {
    fetchMock.mockResolvedValueOnce(
      json({ error: { code: 'last_active_admin', message: 'nope' } }, 409)
    );
    await expect(setUserStatus('u1', 'disabled')).rejects.toMatchObject({
      code: 'last_active_admin',
      status: 409,
    });
  });

  it('AdminApiError carries Retry-After on 429', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'slow' } }), {
        status: 429,
        headers: { 'Content-Type': 'application/json', 'Retry-After': '30' },
      })
    );
    try {
      await getStats();
      throw new Error('should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(AdminApiError);
      expect((e as AdminApiError).retryAfter).toBe(30);
    }
  });
});
