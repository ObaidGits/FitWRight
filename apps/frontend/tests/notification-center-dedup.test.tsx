import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const listMock = vi.fn();
const unreadCountMock = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/api/notifications', () => ({
  notificationsApi: {
    list: (...args: unknown[]) => listMock(...args),
    unreadCount: (...args: unknown[]) => unreadCountMock(...args),
    dismiss: vi.fn(),
    markRead: vi.fn(),
    markAllRead: vi.fn(),
  },
}));

import { NotificationCenter } from '@/components/notifications/notification-center';

describe('NotificationCenter request ownership', () => {
  afterEach(() => vi.clearAllMocks());

  it('deduplicates list and unread requests across responsive shell mounts', async () => {
    listMock.mockResolvedValue([]);
    unreadCountMock.mockResolvedValue({
      unread: 0,
      transport: 'polling',
      pollIntervalSeconds: 60,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });

    const view = render(
      <QueryClientProvider client={client}>
        <NotificationCenter />
        <NotificationCenter />
      </QueryClientProvider>
    );

    await waitFor(() => expect(unreadCountMock).toHaveBeenCalledOnce());
    expect(listMock).not.toHaveBeenCalled();

    const triggers = view.getAllByRole('button', { name: 'Notifications' });
    fireEvent.pointerDown(triggers[0], { button: 0, ctrlKey: false });
    await waitFor(() => expect(listMock).toHaveBeenCalledOnce());

    // The second responsive mount reuses the fresh shared list rather than
    // issuing another request when its menu opens.
    fireEvent.pointerDown(triggers[1], { button: 0, ctrlKey: false });
    await waitFor(() => expect(listMock).toHaveBeenCalledOnce());
  });
});
