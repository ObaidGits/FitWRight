import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Admin mutation hooks (Task 8.3) - the reversible status toggle must be
 * OPTIMISTIC with ROLLBACK (R10.4/R13.3): the cached user-list pages flip
 * immediately, and revert if the server rejects. Network layer is mocked.
 */

vi.mock('@/lib/api/admin', () => {
  class AdminApiError extends Error {
    code: string;
    status: number;
    constructor(code: string, message: string, status: number) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }
  return {
    AdminApiError,
    adminApi: { setUserStatus: vi.fn() },
  };
});

import { adminApi } from '@/lib/api/admin';
import { adminKeys, useSetUserStatus } from '@/features/admin/hooks';

const LIST_PARAMS = { limit: 25 };

function seededClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(adminKeys.users(LIST_PARAMS), {
    items: [
      {
        id: 'u1',
        name: 'A',
        email: 'a@x.io',
        role: 'user',
        status: 'active',
        emailVerified: true,
        createdAt: 't',
        resumeCount: 0,
        applicationCount: 0,
      },
    ],
    nextCursor: null,
  });
  return qc;
}

function wrapperFor(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function statusOf(qc: QueryClient): string {
  const data = qc.getQueryData(adminKeys.users(LIST_PARAMS)) as { items: { status: string }[] };
  return data.items[0].status;
}

describe('useSetUserStatus - optimistic + rollback', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('optimistically flips the cached row before the server responds', async () => {
    const qc = seededClient();
    // A promise we control so we can observe the optimistic (pre-resolve) state.
    let resolve!: (v: unknown) => void;
    (adminApi.setUserStatus as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => (resolve = r))
    );

    const { result } = renderHook(() => useSetUserStatus(), { wrapper: wrapperFor(qc) });
    result.current.mutate({ id: 'u1', status: 'disabled' });

    // Optimistic patch lands before the server resolves.
    await waitFor(() => expect(statusOf(qc)).toBe('disabled'));

    resolve({ changed: true });
    await waitFor(() => expect(result.current.isPending).toBe(false));
  });

  it('rolls back the cached row when the server rejects', async () => {
    const qc = seededClient();
    (adminApi.setUserStatus as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('server said no')
    );

    const { result } = renderHook(() => useSetUserStatus(), { wrapper: wrapperFor(qc) });
    result.current.mutate({ id: 'u1', status: 'disabled' });

    // After the failure settles, the optimistic change is reverted.
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(statusOf(qc)).toBe('active');
  });
});
