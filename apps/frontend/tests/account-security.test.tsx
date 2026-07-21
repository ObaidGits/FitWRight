import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Settings -> Account security (Task 8.3): the device list renders active
 * sessions and revokes a non-current one. The auth API + session are mocked.
 */

const { AuthApiError } = vi.hoisted(() => {
  class AuthApiError extends Error {
    code: string;
    status: number;
    constructor(code: string, message: string, status = 400) {
      super(message);
      this.name = 'AuthApiError';
      this.code = code;
      this.status = status;
    }
  }
  return { AuthApiError };
});

const listSessionsMock = vi.fn();
const revokeSessionMock = vi.fn().mockResolvedValue(undefined);

vi.mock('@/lib/api/auth', () => ({
  AuthApiError,
  authApi: {
    listSessions: () => listSessionsMock(),
    revokeSession: (id: string) => revokeSessionMock(id),
    logoutAll: vi.fn().mockResolvedValue(undefined),
    stepUp: vi.fn().mockResolvedValue({ id: 'u1' }),
    changePassword: vi.fn(),
    beginEmailChange: vi.fn(),
  },
}));

vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ user: { email: 'ada@example.com' }, signOut: vi.fn() }),
}));

import { AccountSecurity } from '@/components/settings/account-security';
import { StepUpProvider } from '@/components/auth/step-up-modal';
import { ToastProvider } from '@/components/atelier/toast';

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <StepUpProvider>
          <AccountSecurity />
        </StepUpProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe('AccountSecurity - device list', () => {
  beforeEach(() => {
    listSessionsMock.mockReset();
    revokeSessionMock.mockClear();
  });
  afterEach(() => vi.clearAllMocks());

  it('lists sessions and revokes a non-current device', async () => {
    const now = new Date().toISOString();
    listSessionsMock.mockResolvedValue([
      { id: 's1', deviceLabel: 'Chrome on macOS', current: false, createdAt: now, lastSeenAt: now },
      { id: 's2', deviceLabel: 'This browser', current: true, createdAt: now, lastSeenAt: now },
    ]);

    renderShell();

    await waitFor(() => expect(screen.getByText('Chrome on macOS')).toBeInTheDocument());
    // Only the non-current session exposes a Revoke button.
    const revokeButtons = screen.getAllByRole('button', { name: 'Revoke' });
    expect(revokeButtons).toHaveLength(1);

    fireEvent.click(revokeButtons[0]);
    await waitFor(() => expect(revokeSessionMock).toHaveBeenCalledWith('s1'));
  });
});
