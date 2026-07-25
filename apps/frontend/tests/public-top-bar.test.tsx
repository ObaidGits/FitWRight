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

// Mode is read at render time; a hoisted getter lets each test flip it.
const modeCfg = vi.hoisted(() => ({ singleUser: false }));
vi.mock('@/lib/config/auth', () => ({
  get SINGLE_USER_MODE() {
    return modeCfg.singleUser;
  },
}));
vi.mock('@/components/layout/account-menu', () => ({
  AccountMenu: () => <div data-testid="account-menu">profile</div>,
}));
vi.mock('@/components/theme/theme-toggle', () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}));

import { PublicTopBar } from '@/components/layout/public-top-bar';

afterEach(() => {
  vi.clearAllMocks();
  modeCfg.singleUser = false; // default: hosted (status-driven) unless a test opts in
});

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

  it('shows Sign in / Sign up immediately while a session retry is loading', () => {
    sessionStatus = 'loading';
    render(<PublicTopBar />);
    expect(screen.queryByTestId('account-menu')).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /sign in/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: /sign up/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('link', { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it('never shows Sign in / Sign up in single-user mode (no login wall)', () => {
    // Even if the session status is momentarily non-authenticated, single-user
    // mode has no login wall - the owner is always "signed in".
    modeCfg.singleUser = true;
    sessionStatus = 'loading';
    render(<PublicTopBar />);
    expect(screen.getByTestId('account-menu')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^sign in$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^sign up$/i })).not.toBeInTheDocument();
  });
});
