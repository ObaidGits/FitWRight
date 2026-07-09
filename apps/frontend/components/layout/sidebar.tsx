'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Search from 'lucide-react/dist/esm/icons/search';
import { cn } from '@/lib/utils';
import { PRIMARY_NAV, TAILOR_HREF } from '@/components/layout/nav-items';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { AccountMenu } from '@/components/layout/account-menu';
import { NotificationCenter } from '@/components/notifications/notification-center';
import { useCommandPalette } from '@/components/command/command-palette';

export function Sidebar() {
  const pathname = usePathname();
  const { open: openCommandPalette } = useCommandPalette();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <aside className="hidden md:flex md:w-60 md:shrink-0 md:flex-col md:border-r md:border-[var(--border)] md:bg-[var(--card)]">
      <div className="flex h-16 items-center gap-2 px-5">
        <Link href="/home" className="flex items-center gap-2 font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] text-xs font-bold text-[var(--primary-foreground)]">
            FW
          </span>
          <span>FitWright</span>
        </Link>
      </div>

      <div className="px-3">
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

      <nav className="mt-4 flex-1 space-y-1 px-3" aria-label="Primary">
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
            </Link>
          );
        })}
      </nav>

      <div className="flex items-center justify-between border-t border-[var(--border)] px-4 py-3">
        <AccountMenu />
        <div className="flex items-center gap-1">
          <NotificationCenter />
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}
