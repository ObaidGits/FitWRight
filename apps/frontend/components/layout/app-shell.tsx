'use client';

/**
 * Authenticated app shell (Task 3.1/3.2). Desktop sidebar + mobile bottom nav,
 * with a mobile top bar for brand/theme. Content area scrolls; a skip link and
 * <main> landmark satisfy accessibility (Req 21).
 */
import * as React from 'react';
import Link from 'next/link';
import { Sidebar } from '@/components/layout/sidebar';
import { BottomNav } from '@/components/layout/bottom-nav';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { AccountMenu } from '@/components/layout/account-menu';
import { NotificationCenter } from '@/components/notifications/notification-center';
import { OfflineIndicator } from '@/components/resilience/offline-indicator';
import { VerifyEmailBanner } from '@/components/auth/verify-email-banner';

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-[var(--radius-at-md)] focus:bg-[var(--card)] focus:px-3 focus:py-2 focus:shadow-[var(--shadow-at-e2)]"
      >
        Skip to content
      </a>

      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <OfflineIndicator />
        <VerifyEmailBanner />
        {/* Mobile top bar */}
        <header className="flex h-14 items-center justify-between border-b border-[var(--border)] bg-[var(--card)] px-4 md:hidden">
          <Link href="/home" className="flex items-center gap-2 font-semibold">
            <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] text-xs font-bold text-[var(--primary-foreground)]">
              FW
            </span>
            FitWright
          </Link>
          <div className="flex items-center gap-1">
            <NotificationCenter />
            <ThemeToggle />
            <AccountMenu />
          </div>
        </header>

        <main id="main-content" className="flex-1 pb-20 md:pb-0">
          <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">{children}</div>
        </main>
      </div>

      <BottomNav />
    </div>
  );
}
