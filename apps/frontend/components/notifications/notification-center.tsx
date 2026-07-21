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
import { notificationsApi, type AppNotification } from '@/lib/api/notifications';

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
  const [open, setOpen] = React.useState(false);
  const [items, setItems] = React.useState<AppNotification[]>([]);
  // Server-authoritative unread badge (O(1) counter) - kept fresh by polling
  // the interval the backend advertises, active tab only.
  const [serverUnread, setServerUnread] = React.useState<number | null>(null);

  const load = React.useCallback(() => {
    notificationsApi
      .list()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  const refreshCount = React.useCallback(() => {
    notificationsApi
      .unreadCount()
      .then((c) => setServerUnread(c.unread))
      .catch(() => setServerUnread(null));
  }, []);

  React.useEffect(() => {
    load();
    refreshCount();
  }, [load, refreshCount]);

  React.useEffect(() => {
    if (open) load();
  }, [open, load]);

  // Poll the unread counter while the tab is visible. Uses the backend's
  // advertised interval (falls back to 60s) and pauses when hidden.
  React.useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;
    async function schedule() {
      try {
        const c = await notificationsApi.unreadCount();
        if (cancelled) return;
        setServerUnread(c.unread);
        const ms = Math.max(15, c.pollIntervalSeconds || 60) * 1000;
        timer = setInterval(() => {
          if (document.visibilityState === 'visible') refreshCount();
        }, ms);
      } catch {
        /* leave badge derived from the list */
      }
    }
    schedule();
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [refreshCount]);

  // Prefer the server counter; fall back to deriving from the loaded list.
  const derivedUnread = items.filter((n) => !n.read).length;
  const unread = serverUnread ?? derivedUnread;

  async function dismiss(id: string) {
    const wasUnread = items.find((n) => n.id === id && !n.read);
    await notificationsApi.dismiss(id).catch(() => undefined);
    setItems((prev) => prev.filter((n) => n.id !== id));
    if (wasUnread) setServerUnread((c) => (c != null ? Math.max(0, c - 1) : c));
  }

  async function markAllRead() {
    const hadUnread = items.some((n) => !n.read);
    if (!hadUnread) return;
    setItems((prev) => prev.map((n) => ({ ...n, read: true })));
    setServerUnread(0);
    await notificationsApi.markAllRead().catch(() => refreshCount());
  }

  function openNode(n: AppNotification) {
    // Mark read on interaction (optimistic), then deep-link if possible.
    if (!n.read) {
      setItems((prev) => prev.map((it) => (it.id === n.id ? { ...it, read: true } : it)));
      setServerUnread((c) => (c != null ? Math.max(0, c - 1) : c));
      notificationsApi.markRead(n.id).catch(() => refreshCount());
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
