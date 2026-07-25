import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SessionProvider, useSession } from '@/lib/context/session';

/**
 * SessionProvider in SINGLE_USER_MODE (the default local env): the owner is
 * always authenticated as admin, with no login wall. It best-effort hydrates
 * ONLY the owner's avatar from the profile endpoint (never `/auth/session`,
 * which 401s the cookieless local owner) and falls back cleanly on failure.
 */

// getProfile resolves the implicit owner in single-user mode; control it here.
const getProfileMock = vi.fn();
vi.mock('@/lib/api/profile', () => ({
  getProfile: () => getProfileMock(),
}));

function Probe() {
  const { status, user, isAdmin } = useSession();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="name">{user?.name}</span>
      <span data-testid="admin">{String(isAdmin)}</span>
      <span data-testid="avatar">{user?.avatarUrl ?? 'none'}</span>
    </div>
  );
}

function renderWithClient(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SessionProvider>{ui}</SessionProvider>
    </QueryClientProvider>
  );
}

describe('SessionProvider - single-user mode', () => {
  it('presents the owner as an authenticated admin (no hydration needed)', () => {
    getProfileMock.mockReturnValue(new Promise(() => {})); // pending
    renderWithClient(<Probe />);
    expect(screen.getByTestId('status').textContent).toBe('authenticated');
    expect(screen.getByTestId('name').textContent).toBe('You');
    expect(screen.getByTestId('admin').textContent).toBe('true');
  });

  it('hydrates the real owner avatar when the profile resolves', async () => {
    getProfileMock.mockResolvedValue({
      headline: null,
      location: null,
      links: [],
      avatar_url: 'https://example.com/me.png',
    });
    renderWithClient(<Probe />);
    // Still authenticated immediately; avatar fills in once the profile loads.
    expect(screen.getByTestId('status').textContent).toBe('authenticated');
    await waitFor(() =>
      expect(screen.getByTestId('avatar').textContent).toBe('https://example.com/me.png')
    );
  });

  it('falls back cleanly when the profile fetch fails (still authenticated)', async () => {
    getProfileMock.mockRejectedValue(new Error('backend down'));
    renderWithClient(<Probe />);
    expect(screen.getByTestId('status').textContent).toBe('authenticated');
    expect(screen.getByTestId('admin').textContent).toBe('true');
    // No avatar, but never blocks access.
    await waitFor(() => expect(screen.getByTestId('avatar').textContent).toBe('none'));
  });
});
