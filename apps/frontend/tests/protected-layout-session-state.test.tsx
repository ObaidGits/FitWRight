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

// The builder used to live in its own `(default)` group with a duplicate SSR
// guard. It now sits in the authenticated `(app)` group (so it renders with the
// sidebar and bottom nav instead of being a dead end), which means THIS layout
// is what protects /builder. The guard contract is unchanged and still asserted
// against a /builder path.
import AppGroupLayout from '@/app/(app)/layout';

describe('protected app-group layout session state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.headers.mockResolvedValue(new Headers({ 'x-pathname': '/builder' }));
    mocks.redirect.mockImplementation((url: string) => {
      throw new Error(`redirect:${url}`);
    });
  });

  it('redirects an authoritative guest instead of treating the state object as a user', async () => {
    mocks.getServerSession.mockResolvedValue({ user: null, resolved: true });

    await expect(AppGroupLayout({ children: null })).rejects.toThrow(
      'redirect:/login?next=%2Fbuilder'
    );
  });

  it('preserves the session during a transient auth-service outage', async () => {
    mocks.getServerSession.mockResolvedValue({ user: null, resolved: false });

    await expect(AppGroupLayout({ children: null })).rejects.toThrow(
      'Authentication service is temporarily unavailable.'
    );
    expect(mocks.redirect).not.toHaveBeenCalled();
  });
});
