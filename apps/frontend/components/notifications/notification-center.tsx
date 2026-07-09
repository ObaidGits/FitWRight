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

  const load = React.useCallback(() => {
    notificationsApi
      .list()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  React.useEffect(() => {
    if (open) load();
  }, [open, load]);

  const unread = items.filter((n) => !n.read).length;

  async function dismiss(id: string) {
    await notificationsApi.dismiss(id).catch(() => undefined);
    setItems((prev) => prev.filter((n) => n.id !== id));
  }

  function openNode(n: AppNotification) {
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
        <DropdownMenuLabel>Notifications</DropdownMenuLabel>
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
                <button
                  onClick={() => openNode(n)}
                  className="min-w-0 flex-1 text-left"
                  disabled={!n.nodeRef}
                >
                  <p className="truncate text-sm text-[var(--foreground)]">{n.message}</p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {relativeTime(n.createdAt)}
                  </p>
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
