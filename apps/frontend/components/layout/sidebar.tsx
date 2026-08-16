'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Search from 'lucide-react/dist/esm/icons/search';
import Heart from 'lucide-react/dist/esm/icons/heart';
import UserRound from 'lucide-react/dist/esm/icons/user-round';
import { cn } from '@/lib/utils';
import { PRIMARY_NAV, TAILOR_HREF } from '@/components/layout/nav-items';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { AccountMenu } from '@/components/layout/account-menu';
import { PlanBadge } from '@/components/billing/plan-badge';
import { NotificationCenter } from '@/components/notifications/notification-center';
import { useCommandPalette } from '@/components/command/command-palette';
import { useFieldSummary } from '@/features/application-fields/hooks';

export function Sidebar() {
  const pathname = usePathname();
  const { open: openCommandPalette } = useCommandPalette();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <aside className="hidden md:flex md:h-full md:w-60 md:shrink-0 md:flex-col md:overflow-hidden md:border-r md:border-[var(--border)] md:bg-[var(--card)]">
      <div className="flex h-16 shrink-0 items-center gap-2 px-5">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] text-xs font-bold text-[var(--primary-foreground)]">
            FW
          </span>
          <span>FitWright</span>
        </Link>
      </div>

      <div className="shrink-0 px-3">
        <Link
          href={TAILOR_HREF}
          className={cn(
            'flex h-10 w-full items-center justify-center gap-2 rounded-[var(--radius-at-md)]',
            'bg-[var(--primary)] text-sm font-medium text-[var(--primary-foreground)] shadow-[var(--shadow-at-e1)]',
            'transition hover:brightness-110'
          )}
        >
          <Sparkles className="h-4 w-4" /> Tailor to a job
        </Link>
        <button
          onClick={openCommandPalette}
          className={cn(
            'mt-2 flex h-9 w-full items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] px-3',
            'text-sm text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)]'
          )}
        >
          <Search className="h-4 w-4" />
          <span className="flex-1 text-left">Search</span>
          <kbd className="rounded bg-[var(--secondary)] px-1.5 py-0.5 text-[10px]">⌘K</kbd>
        </button>
      </div>

      <nav className="mt-4 min-h-0 flex-1 space-y-1 overflow-y-auto px-3" aria-label="Primary">
        {PRIMARY_NAV.map((item) => {
          const active = isActive(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex items-center gap-3 rounded-[var(--radius-at-md)] px-3 py-2 text-sm font-medium transition-colors',
                active
                  ? 'bg-[var(--accent)] text-[var(--foreground)]'
                  : 'text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]'
              )}
            >
              <Icon className="h-[18px] w-[18px]" />
              {item.label}
              {item.href === '/discovery' && <DiscoverBadge />}
              {item.href === '/answers' && <AnswersBadge />}
            </Link>
          );
        })}

        {/* Profile - the canonical career document. A distinct destination
            below the core workflow nav (kept out of PRIMARY_NAV so the mobile
            bottom nav, which reads that list by index, is unaffected). */}
        <Link
          href="/profile"
          aria-current={isActive('/profile') ? 'page' : undefined}
          className={cn(
            'flex items-center gap-3 rounded-[var(--radius-at-md)] px-3 py-2 text-sm font-medium transition-colors',
            isActive('/profile')
              ? 'bg-[var(--accent)] text-[var(--foreground)]'
              : 'text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]'
          )}
        >
          <UserRound className="h-[18px] w-[18px]" />
          Profile
        </Link>

        {/* Contact the developer - distinct destination below core workflow nav. */}
        <Link
          href="/contact"
          aria-current={isActive('/contact') ? 'page' : undefined}
          className={cn(
            'flex items-center gap-3 rounded-[var(--radius-at-md)] px-3 py-2 text-sm font-medium transition-colors',
            isActive('/contact')
              ? 'bg-[var(--accent)] text-[var(--foreground)]'
              : 'text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]'
          )}
        >
          <Heart className="h-[18px] w-[18px]" />
          Contact
        </Link>
      </nav>

      <div className="flex shrink-0 items-center justify-between border-t border-[var(--border)] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <AccountMenu />
          {/* Renders nothing on the free tier - see PlanBadge. */}
          <PlanBadge />
        </div>
        <div className="flex items-center gap-1">
          <NotificationCenter />
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}

/** Tiny badge showing unseen job count next to "Discover" nav item. */
function DiscoverBadge() {
  // Lazy-loaded to avoid circular deps; only renders client-side
  const [count, setCount] = React.useState(0);
  React.useEffect(() => {
    fetch('/api/v1/discovery/feed/unseen', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : { unseen: 0 }))
      .then((d) => setCount(d.unseen || 0))
      .catch(() => {});
    const interval = setInterval(() => {
      fetch('/api/v1/discovery/feed/unseen', { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : { unseen: 0 }))
        .then((d) => setCount(d.unseen || 0))
        .catch(() => {});
    }, 60_000);
    return () => clearInterval(interval);
  }, []);
  if (count === 0) return null;
  return (
    <span className="ml-auto flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[10px] font-bold text-primary-foreground">
      {count > 99 ? '99+' : count}
      <span className="sr-only">{count === 1 ? ' new job' : ' new jobs'}</span>
    </span>
  );
}

/**
 * Questions from application forms waiting for an answer.
 *
 * Uses the shared query so answering one in the Answers page updates this badge
 * through the invalidation that already exists, instead of leaving a stale count
 * until the next poll.
 */
function AnswersBadge() {
  const summary = useFieldSummary();
  const count = summary.data?.needs_answer ?? 0;
  if (count === 0) return null;
  return (
    <span className="ml-auto flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--at-warning)] px-1.5 text-[10px] font-bold text-[var(--background)]">
      {count > 99 ? '99+' : count}
      <span className="sr-only">
        {count === 1 ? ' question needs an answer' : ' questions need an answer'}
      </span>
    </span>
  );
}
