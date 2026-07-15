import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * SessionProvider in hosted (multi-user) mode: the loading → authenticated /
 * guest states from hydrating `GET /auth/session`, plus the 401 interceptor
 * (clear session + multi-tab broadcast + redirect to /login?next).
 */

const replaceMock = vi.fn();
let capturedHandler: (() => void) | null = null;

vi.mock('@/lib/config/auth', () => ({
  SINGLE_USER_MODE: false,
  CSRF_COOKIE_NAME: 'csrf',
  SESSION_COOKIE_NAMES: ['__Host-session', 'session'],
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/home',
}));

const fetchSessionMock = vi.fn();
vi.mock('@/lib/api/auth', () => ({
  fetchSession: () => fetchSessionMock(),
  logout: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@/lib/api/client', () => ({
  setUnauthorizedHandler: (h: (() => void) | null) => {
    capturedHandler = h;
  },
}));

// Imported after the mocks are registered.
import { SessionProvider, useSession } from '@/lib/context/session';

const USER = {
  id: 'u1',
  name: 'Ada',
  email: 'ada@example.com',
  role: 'user',
  status: 'active',
  emailVerified: true,
  aal: 'aal1',
} as const;

function Probe() {
  const { status, user } = useSession();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="name">{user?.name ?? ''}</span>
    </div>
  );
}

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('SessionProvider — multi-user mode', () => {
  beforeEach(() => {
    replaceMock.mockClear();
    fetchSessionMock.mockReset();
    capturedHandler = null;
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('is authenticated immediately when seeded with an SSR user', () => {
    fetchSessionMock.mockResolvedValue(USER);
    renderWithClient(
      <SessionProvider initialUser={USER}>
        <Probe />
      </SessionProvider>
    );
    expect(screen.getByTestId('status').textContent).toBe('authenticated');
    expect(screen.getByTestId('name').textContent).toBe('Ada');
  });

  it('shows loading then authenticated when hydrating from the backend', async () => {
    let resolve!: (u: typeof USER) => void;
    fetchSessionMock.mockReturnValue(new Promise((r) => (resolve = r)));
    renderWithClient(
      <SessionProvider initialUser={null}>
        <Probe />
      </SessionProvider>
    );
    expect(screen.getByTestId('status').textContent).toBe('loading');
    resolve(USER);
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'));
  });

  it('resolves to guest when the backend reports no session', async () => {
    fetchSessionMock.mockResolvedValue(null);
    renderWithClient(
      <SessionProvider initialUser={null}>
        <Probe />
      </SessionProvider>
    );
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('guest'));
  });

  it('the 401 interceptor redirects to /login?next and signals other tabs', async () => {
    fetchSessionMock.mockResolvedValue(USER);
    renderWithClient(
      <SessionProvider initialUser={USER}>
        <Probe />
      </SessionProvider>
    );
    await waitFor(() => expect(capturedHandler).toBeTypeOf('function'));
    capturedHandler!();
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock.mock.calls[0][0]).toMatch(/^\/login\?next=/);
    // Multi-tab logout signal written to storage for other tabs.
    expect(window.localStorage.getItem('fitwright-auth-logout')).toBeTruthy();
  });
});
