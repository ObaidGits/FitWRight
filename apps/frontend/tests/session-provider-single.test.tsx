import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SessionProvider, useSession } from '@/lib/context/session';

/**
 * SessionProvider in SINGLE_USER_MODE (the default local env): the owner is
 * always authenticated as admin, with no backend hydration — local zero-config
 * boot is unchanged (R14.3/15.5).
 */
function Probe() {
  const { status, user, isAdmin } = useSession();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="name">{user?.name}</span>
      <span data-testid="admin">{String(isAdmin)}</span>
    </div>
  );
}

describe('SessionProvider — single-user mode', () => {
  it('presents the owner as an authenticated admin', () => {
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );
    expect(screen.getByTestId('status').textContent).toBe('authenticated');
    expect(screen.getByTestId('name').textContent).toBe('You');
    expect(screen.getByTestId('admin').textContent).toBe('true');
  });
});
