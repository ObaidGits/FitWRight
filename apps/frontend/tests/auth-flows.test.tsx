import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Verify-email / forgot / reset flow wiring (Task 8.3 / R5, R6, R15.4). The
 * auth API + navigation + session are mocked.
 */

const replaceMock = vi.fn();
const refreshMock = vi.fn().mockResolvedValue(undefined);
let searchParams = new URLSearchParams('');

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

const forgotPasswordMock = vi.fn().mockResolvedValue(undefined);
const resetPasswordMock = vi.fn().mockResolvedValue({ id: 'u1' });
const confirmVerificationMock = vi.fn().mockResolvedValue(undefined);
const requestVerificationMock = vi.fn().mockResolvedValue(undefined);

vi.mock('@/lib/api/auth', () => ({
  AuthApiError,
  authApi: {
    forgotPassword: (e: string) => forgotPasswordMock(e),
    resetPassword: (i: unknown) => resetPasswordMock(i),
    confirmVerification: (t: string) => confirmVerificationMock(t),
    requestVerification: (e?: string) => requestVerificationMock(e),
  },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  useSearchParams: () => searchParams,
}));

vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ refresh: refreshMock, user: null }),
}));

import { ForgotCard } from '@/components/auth/forgot-card';
import { ResetCard } from '@/components/auth/reset-card';
import { VerifyEmailCard } from '@/components/auth/verify-email-card';

describe('auth flows', () => {
  beforeEach(() => {
    replaceMock.mockClear();
    refreshMock.mockClear();
    forgotPasswordMock.mockClear();
    resetPasswordMock.mockClear();
    confirmVerificationMock.mockClear();
    requestVerificationMock.mockClear();
    searchParams = new URLSearchParams('');
  });
  afterEach(() => vi.clearAllMocks());

  it('forgot: submits an email and shows a uniform confirmation', async () => {
    render(<ForgotCard />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } });
    fireEvent.click(screen.getByRole('button', { name: /send reset link/i }));
    await waitFor(() => expect(forgotPasswordMock).toHaveBeenCalledWith('a@b.com'));
    expect(screen.getByText(/reset link is on its way/i)).toBeInTheDocument();
  });

  it('reset: validates match + length, then sets the new password and redirects', async () => {
    searchParams = new URLSearchParams('token=tkn');
    render(<ResetCard />);
    fireEvent.change(screen.getByLabelText('New password'), {
      target: { value: 'a-long-enough-passphrase' },
    });
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'a-long-enough-passphrase' },
    });
    fireEvent.click(screen.getByRole('button', { name: /update password/i }));
    await waitFor(() =>
      expect(resetPasswordMock).toHaveBeenCalledWith({
        token: 'tkn',
        password: 'a-long-enough-passphrase',
      })
    );
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith('/home'));
  });

  it('reset: blocks mismatched passwords', () => {
    searchParams = new URLSearchParams('token=tkn');
    render(<ResetCard />);
    fireEvent.change(screen.getByLabelText('New password'), {
      target: { value: 'a-long-enough-passphrase' },
    });
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'different-passphrase-x' },
    });
    fireEvent.click(screen.getByRole('button', { name: /update password/i }));
    expect(screen.getByRole('alert').textContent).toMatch(/do not match/i);
    expect(resetPasswordMock).not.toHaveBeenCalled();
  });

  it('verify: redeems a token on the landing page', async () => {
    searchParams = new URLSearchParams('token=vtok');
    render(<VerifyEmailCard />);
    await waitFor(() => expect(confirmVerificationMock).toHaveBeenCalledWith('vtok'));
    await waitFor(() => expect(screen.getByText(/your email is verified/i)).toBeInTheDocument());
  });

  it('verify: pending view resends a link', async () => {
    searchParams = new URLSearchParams('email=a@b.com&sent=1');
    render(<VerifyEmailCard />);
    fireEvent.click(screen.getByRole('button', { name: /resend confirmation/i }));
    await waitFor(() => expect(requestVerificationMock).toHaveBeenCalled());
  });
});
