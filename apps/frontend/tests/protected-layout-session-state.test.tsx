import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  headers: vi.fn(),
  redirect: vi.fn(),
}));

vi.mock('@/lib/api/session-server', () => ({
  getServerSession: mocks.getServerSession,
}));
vi.mock('next/headers', () => ({ headers: mocks.headers }));
vi.mock('next/navigation', () => ({ redirect: mocks.redirect }));

import DefaultLayout from '@/app/(default)/layout';

describe('protected builder layout session state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.headers.mockResolvedValue(new Headers({ 'x-pathname': '/builder' }));
    mocks.redirect.mockImplementation((url: string) => {
      throw new Error(`redirect:${url}`);
    });
  });

  it('redirects an authoritative guest instead of treating the state object as a user', async () => {
    mocks.getServerSession.mockResolvedValue({ user: null, resolved: true });

    await expect(DefaultLayout({ children: null })).rejects.toThrow(
      'redirect:/login?next=%2Fbuilder'
    );
  });

  it('preserves the session during a transient auth-service outage', async () => {
    mocks.getServerSession.mockResolvedValue({ user: null, resolved: false });

    await expect(DefaultLayout({ children: null })).rejects.toThrow(
      'Authentication service is temporarily unavailable.'
    );
    expect(mocks.redirect).not.toHaveBeenCalled();
  });
});
