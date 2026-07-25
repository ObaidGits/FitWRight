'use client';

/**
 * NotificationCenter (Task 21 / Req 33.2, 33.3).
 *
 * A bell with an unread badge that opens a dismissible list. It reads from the
 * typed `notifications` interface so persistent/scheduled items (interview
 * tomorrow, key expired, follow-up due) can be wired later with no UI change.
 * Transient events (export finished, AI failed, parsing complete) continue to
 * use the toast system directly. Items reference a node but never leak content.
 */
import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Bell from 'lucide-react/dist/esm/icons/bell';
import Inbox from 'lucide-react/dist/esm/icons/inbox';
import { useRouter } from 'next/navigation';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/atelier/dropdown-menu';
import { Button } from '@/components/atelier/button';
import { cn } from '@/lib/utils';
import { notificationsApi, type AppNotification, type UnreadCount } from '@/lib/api/notifications';
import { queryKeys } from '@/lib/query/client';

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return new Date(iso).toLocaleDateString();
}

export function NotificationCenter() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);

  // Both responsive shell instances observe the same query records. The full
  // list is loaded only when a menu opens; the unread badge remains the sole
  // background poll. TanStack Query deduplicates simultaneous opens and reuses
  // the fresh list across the desktop sidebar and mobile header.
  const listQuery = useQuery<AppNotification[]>({
    queryKey: queryKeys.notificationsList,
    queryFn: notificationsApi.list,
    staleTime: 30_000,
    enabled: open,
  });
  const countQuery = useQuery<UnreadCount>({
    queryKey: queryKeys.notificationsUnread,
    queryFn: notificationsApi.unreadCount,
    staleTime: 15_000,
    refetchInterval: (query) => {
      const seconds = query.state.data?.pollIntervalSeconds ?? 60;
      return Math.max(15, seconds) * 1000;
    },
    refetchIntervalInBackground: false,
  });

  const items = listQuery.data ?? [];
  const serverUnread = countQuery.data?.unread ?? null;

  // Prefer the server's O(1) counter; derive from the list while unavailable.
  const derivedUnread = items.filter((n) => !n.read).length;
  const unread = serverUnread ?? derivedUnread;

  const setItems = React.useCallback(
    (updater: (current: AppNotification[]) => AppNotification[]) => {
      queryClient.setQueryData<AppNotification[]>(queryKeys.notificationsList, (current) =>
        updater(current ?? [])
      );
    },
    [queryClient]
  );
  const setUnread = React.useCallback(
    (updater: (current: number) => number) => {
      queryClient.setQueryData<UnreadCount>(queryKeys.notificationsUnread, (current) =>
        current ? { ...current, unread: updater(current.unread) } : current
      );
    },
    [queryClient]
  );

  async function dismiss(id: string) {
    const wasUnread = items.some((n) => n.id === id && !n.read);
    setItems((current) => current.filter((n) => n.id !== id));
    if (wasUnread) setUnread((count) => Math.max(0, count - 1));
    try {
      await notificationsApi.dismiss(id);
    } catch {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.notificationsList }),
        queryClient.invalidateQueries({ queryKey: queryKeys.notificationsUnread }),
      ]);
    }
  }

  async function markAllRead() {
    if (!items.some((n) => !n.read)) return;
    setItems((current) => current.map((n) => ({ ...n, read: true })));
    setUnread(() => 0);
    try {
      await notificationsApi.markAllRead();
    } catch {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.notificationsList }),
        queryClient.invalidateQueries({ queryKey: queryKeys.notificationsUnread }),
      ]);
    }
  }

  function openNode(n: AppNotification) {
    if (!n.read) {
      setItems((current) =>
        current.map((item) => (item.id === n.id ? { ...item, read: true } : item))
      );
      setUnread((count) => Math.max(0, count - 1));
      void notificationsApi.markRead(n.id).catch(() => {
        void queryClient.invalidateQueries({ queryKey: queryKeys.notificationsList });
        void queryClient.invalidateQueries({ queryKey: queryKeys.notificationsUnread });
      });
    }
    if (!n.nodeRef) return;
    const href =
      n.nodeRef.type === 'resume' ? `/resumes/${n.nodeRef.id}` : `/applications/${n.nodeRef.id}`;
    setOpen(false);
    router.push(href);
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        >
          <Bell className="h-[18px] w-[18px]" />
          {unread > 0 && (
            <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--destructive)] px-1 text-[10px] font-semibold text-[var(--destructive-foreground)]">
              {unread > 9 ? '9+' : unread}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <div className="flex items-center justify-between px-2">
          <DropdownMenuLabel>Notifications</DropdownMenuLabel>
          {unread > 0 && (
            <button
              onClick={(e) => {
                e.preventDefault();
                void markAllRead();
              }}
              className="text-xs font-medium text-[var(--primary)] hover:underline"
            >
              Mark all read
            </button>
          )}
        </div>
        <DropdownMenuSeparator />
        {items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
            <Inbox className="h-6 w-6 text-[var(--muted-foreground)]" />
            <p className="text-sm text-[var(--muted-foreground)]">You&apos;re all caught up</p>
          </div>
        ) : (
          <ul className="max-h-80 overflow-y-auto">
            {items.map((n) => (
              <li
                key={n.id}
                className="flex items-start gap-2 border-b border-[var(--border)] px-3 py-2.5 last:border-0"
              >
                <button onClick={() => openNode(n)} className="min-w-0 flex-1 text-left">
                  <span className="flex items-center gap-1.5">
                    {!n.read && (
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--primary)]"
                        aria-hidden
                      />
                    )}
                    <span
                      className={cn(
                        'truncate text-sm',
                        n.read
                          ? 'text-[var(--muted-foreground)]'
                          : 'font-medium text-[var(--foreground)]'
                      )}
                    >
                      {n.message}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-xs text-[var(--muted-foreground)]">
                    {relativeTime(n.createdAt)}
                  </span>
                </button>
                <button
                  onClick={() => dismiss(n.id)}
                  className="shrink-0 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                  aria-label="Dismiss notification"
                >
                  Dismiss
                </button>
              </li>
            ))}
          </ul>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
