import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Login/signup card wiring (Task 8.3): inline validation, a single uniform
 * error banner, and the success redirect. The auth API + navigation + session
 * are mocked so nothing hits the network.
 */

const replaceMock = vi.fn();
const refreshMock = vi.fn().mockResolvedValue(undefined);

const { AuthApiError } = vi.hoisted(() => {
  class AuthApiError extends Error {
    code: string;
    status: number;
    retryAfter?: number;
    constructor(code: string, message: string, status = 400) {
      super(message);
      this.name = 'AuthApiError';
      this.code = code;
      this.status = status;
    }
  }
  return { AuthApiError };
});
const loginMock = vi.fn();
const signupMock = vi.fn();

vi.mock('@/lib/api/auth', () => ({
  AuthApiError,
  authApi: {
    login: (...a: unknown[]) => loginMock(...a),
    signup: (...a: unknown[]) => signupMock(...a),
    oauthStartUrl: (p: string, n?: string) => `/api/v1/auth/oauth/${p}/start?next=${n}`,
  },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ refresh: refreshMock }),
}));

import { AuthCard } from '@/components/auth/auth-card';

describe('AuthCard', () => {
  beforeEach(() => {
    replaceMock.mockClear();
    refreshMock.mockClear();
    loginMock.mockReset();
    signupMock.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it('rejects an invalid email before calling the API', () => {
    render(<AuthCard mode="login" />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'not-an-email' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'whatever' } });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(screen.getByRole('alert').textContent).toMatch(/valid email/i);
    expect(loginMock).not.toHaveBeenCalled();
  });

  it('shows a uniform error banner on invalid credentials', async () => {
    loginMock.mockRejectedValue(
      new AuthApiError('invalid_credentials', 'Invalid email or password.', 401)
    );
    render(<AuthCard mode="login" />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secretpw' } });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toBe('Invalid email or password.')
    );
  });

  it('redirects on a successful login', async () => {
    loginMock.mockResolvedValue({ id: 'u1' });
    render(<AuthCard mode="login" />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secretpw' } });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith('/home'));
    expect(refreshMock).toHaveBeenCalled();
  });

  it('enforces the 12-char minimum on signup', () => {
    render(<AuthCard mode="signup" />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Ada' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));
    expect(screen.getByRole('alert').textContent).toMatch(/at least 12/i);
    expect(signupMock).not.toHaveBeenCalled();
  });

  it('routes to /verify when signup is pending verification', async () => {
    signupMock.mockResolvedValue({ user: null, pendingVerification: true });
    render(<AuthCard mode="signup" />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Ada' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'a-long-enough-passphrase' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith(
        expect.stringContaining('/verify?email=ada%40example.com')
      )
    );
  });
});
