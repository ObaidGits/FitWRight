import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

/**
 * Dashboard shell architecture invariants (regression guard):
 * - the shell root is exactly one viewport tall and never scrolls itself,
 * - the sidebar sits outside the scroll region (always visible),
 * - <main> is the single vertical scroll region and is focusable via the skip
 *   link. These guarantee the fixed-sidebar / scrolling-content behavior.
 */

vi.mock('@/components/command/command-palette', () => ({
  useCommandPalette: () => ({ open: vi.fn() }),
}));
vi.mock('@/components/layout/sidebar', () => ({
  Sidebar: () => <aside data-testid="sidebar">nav</aside>,
}));
vi.mock('@/components/layout/bottom-nav', () => ({
  BottomNav: () => <nav data-testid="bottom-nav" />,
}));
vi.mock('@/components/theme/theme-toggle', () => ({ ThemeToggle: () => <div /> }));
vi.mock('@/components/layout/account-menu', () => ({ AccountMenu: () => <div /> }));
vi.mock('@/components/notifications/notification-center', () => ({
  NotificationCenter: () => <div />,
}));
vi.mock('@/components/resilience/offline-indicator', () => ({ OfflineIndicator: () => <div /> }));
vi.mock('@/components/auth/verify-email-banner', () => ({ VerifyEmailBanner: () => <div /> }));

import { AppShell } from '@/components/layout/app-shell';

describe('AppShell layout architecture', () => {
  it('makes the shell viewport-height and non-scrolling', () => {
    const { container } = render(<AppShell>content</AppShell>);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('h-dvh');
    expect(root.className).toContain('overflow-hidden');
  });

  it('renders <main> as the single focusable vertical scroll region', () => {
    render(<AppShell>content</AppShell>);
    const main = document.getElementById('main-content')!;
    expect(main.tagName).toBe('MAIN');
    expect(main.className).toContain('overflow-y-auto');
    expect(main.className).toContain('flex-1');
    // Focusable so the skip link can move focus into content.
    expect(main.getAttribute('tabindex')).toBe('-1');
  });

  it('keeps the sidebar outside <main> so it never scrolls with content', () => {
    const { getByTestId } = render(<AppShell>content</AppShell>);
    const sidebar = getByTestId('sidebar');
    const main = document.getElementById('main-content')!;
    expect(main.contains(sidebar)).toBe(false);
  });
});
