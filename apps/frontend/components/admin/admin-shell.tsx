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
import Users from 'lucide-react/dist/esm/icons/users';
import ChartLine from 'lucide-react/dist/esm/icons/chart-line';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import { useSession } from '@/lib/context/session';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { cn } from '@/lib/utils';

const NAV = [
  { href: '/admin', label: 'Overview', icon: Gauge },
  { href: '/admin/users', label: 'Users', icon: Users },
  { href: '/admin/analytics', label: 'Analytics', icon: ChartLine },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useSession();
  const pathname = usePathname();

  return (
    <div className="atelier flex min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--card)] md:flex">
        <div className="flex h-16 items-center gap-2 px-5 font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--at-ai)] text-xs font-bold text-white">
            FW
          </span>
          Admin
        </div>
        <nav className="flex-1 space-y-1 px-3" aria-label="Admin">
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
        <div className="flex items-center justify-between border-t border-[var(--border)] px-4 py-3">
          <Link
            href="/home"
            className="flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <ArrowLeft className="h-4 w-4" /> Exit admin
          </Link>
          <ThemeToggle />
        </div>
      </aside>

      <main className="flex-1">
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
      </main>
    </div>
  );
}
