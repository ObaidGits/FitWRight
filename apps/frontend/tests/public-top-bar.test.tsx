import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

/**
 * Public top bar auth awareness: a signed-in visitor must see a Dashboard
 * shortcut + profile menu (never Sign in/Sign up); a guest sees the auth links;
 * during hydration neither is shown (no wrong-state flash).
 */

let sessionStatus: 'authenticated' | 'guest' | 'loading' = 'guest';
vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ status: sessionStatus }),
}));
vi.mock('@/components/layout/account-menu', () => ({
  AccountMenu: () => <div data-testid="account-menu">profile</div>,
}));
vi.mock('@/components/theme/theme-toggle', () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}));

import { PublicTopBar } from '@/components/layout/public-top-bar';

afterEach(() => vi.clearAllMocks());

describe('PublicTopBar', () => {
  it('shows Sign in / Sign up for a guest', () => {
    sessionStatus = 'guest';
    render(<PublicTopBar />);
    expect(screen.getAllByRole('link', { name: /sign in/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: /sign up/i }).length).toBeGreaterThan(0);
    expect(screen.queryByTestId('account-menu')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it('shows a Dashboard shortcut + profile menu when signed in (no auth links)', () => {
    sessionStatus = 'authenticated';
    render(<PublicTopBar />);
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/home');
    expect(screen.getByTestId('account-menu')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^sign in$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^sign up$/i })).not.toBeInTheDocument();
  });

  it('shows neither during hydration (loading)', () => {
    sessionStatus = 'loading';
    render(<PublicTopBar />);
    expect(screen.queryByTestId('account-menu')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /sign in/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /dashboard/i })).not.toBeInTheDocument();
  });
});
