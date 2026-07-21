'use client';

/**
 * Admin shell chrome (Task 15 / Req 8, auth wiring Task 8.1).
 *
 * The client presentation for the admin area. Access is enforced
 * SERVER-SIDE by the `admin/layout` SSR guard (and, when wired, by the backend
 * on every admin API). The `isAdmin` check here is defense-in-depth UX only.
 */
import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import Gauge from 'lucide-react/dist/esm/icons/gauge';
import HeartPulse from 'lucide-react/dist/esm/icons/heart-pulse';
import Users from 'lucide-react/dist/esm/icons/users';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Database from 'lucide-react/dist/esm/icons/database';
import ScrollText from 'lucide-react/dist/esm/icons/scroll-text';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import { useSession } from '@/lib/context/session';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { cn } from '@/lib/utils';

const NAV = [
  { href: '/admin', label: 'Overview', icon: Gauge },
  { href: '/admin/health', label: 'Health', icon: HeartPulse },
  { href: '/admin/users', label: 'Users', icon: Users },
  { href: '/admin/ai', label: 'AI', icon: Sparkles },
  { href: '/admin/storage', label: 'Storage', icon: Database },
  { href: '/admin/audit', label: 'Audit', icon: ScrollText },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useSession();
  const pathname = usePathname();

  return (
    // Same fixed-sidebar / scrolling-content shell as the main app (single
    // viewport tall; the shell never scrolls; only the content region does).
    <div className="atelier flex h-dvh overflow-hidden bg-[var(--background)] text-[var(--foreground)]">
      <aside className="hidden w-56 shrink-0 flex-col overflow-hidden border-r border-[var(--border)] bg-[var(--card)] md:flex md:h-full">
        <div className="flex h-16 shrink-0 items-center gap-2 px-5 font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai)] text-xs font-bold text-white">
            FW
          </span>
          Admin
        </div>
        <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3" aria-label="Admin">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex items-center gap-3 rounded-[var(--radius-at-md)] px-3 py-2 text-sm font-medium',
                  active
                    ? 'bg-[var(--accent)] text-[var(--foreground)]'
                    : 'text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]'
                )}
              >
                <Icon className="h-[18px] w-[18px]" /> {label}
              </Link>
            );
          })}
        </nav>
        <div className="flex shrink-0 items-center justify-between border-t border-[var(--border)] px-4 py-3">
          <Link
            href="/home"
            className="flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <ArrowLeft className="h-4 w-4" /> Exit admin
          </Link>
          <ThemeToggle />
        </div>
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Mobile admin nav - the sidebar is desktop-only, so provide a
            horizontally-scrollable tab bar + exit link on small screens. Pinned
            above the scroll region. */}
        <div className="shrink-0 border-b border-[var(--border)] bg-[var(--card)] md:hidden">
          <div className="flex items-center justify-between px-4 py-3">
            <span className="flex items-center gap-2 font-semibold">
              <span className="flex h-6 w-6 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai)] text-[10px] font-bold text-white">
                FW
              </span>
              Admin
            </span>
            <div className="flex items-center gap-2">
              <Link
                href="/home"
                className="flex items-center gap-1 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              >
                <ArrowLeft className="h-3.5 w-3.5" /> Exit
              </Link>
              <ThemeToggle />
            </div>
          </div>
          <nav className="flex gap-1 overflow-x-auto px-2 pb-2" aria-label="Admin">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'flex shrink-0 items-center gap-1.5 rounded-[var(--radius-at-md)] px-3 py-1.5 text-sm font-medium',
                    active
                      ? 'bg-[var(--accent)] text-[var(--foreground)]'
                      : 'text-[var(--muted-foreground)]'
                  )}
                >
                  <Icon className="h-4 w-4" /> {label}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* The single vertical scroll region for the admin area. */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain">
          <div className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8">
            {isAdmin ? (
              children
            ) : (
              <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] p-10 text-center">
                <h1 className="text-lg font-semibold">Admin access required</h1>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                  You don&apos;t have permission to view this area.
                </p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
