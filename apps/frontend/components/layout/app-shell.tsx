'use client';

/**
 * Authenticated app shell (Task 3.1/3.2). Desktop sidebar + mobile bottom nav,
 * with a mobile top bar for brand/theme. Content area scrolls; a skip link and
 * <main> landmark satisfy accessibility (Req 21).
 */
import * as React from 'react';
import Link from 'next/link';
import Search from 'lucide-react/dist/esm/icons/search';
import { Sidebar } from '@/components/layout/sidebar';
import { BottomNav } from '@/components/layout/bottom-nav';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { AccountMenu } from '@/components/layout/account-menu';
import { NotificationCenter } from '@/components/notifications/notification-center';
import { OfflineIndicator } from '@/components/resilience/offline-indicator';
import { VerifyEmailBanner } from '@/components/auth/verify-email-banner';
import { Button } from '@/components/atelier/button';
import { useCommandPalette } from '@/components/command/command-palette';

export function AppShell({ children }: { children: React.ReactNode }) {
  const { open: openCommandPalette } = useCommandPalette();
  return (
    // Dashboard shell: exactly one viewport tall (`h-dvh` handles mobile browser
    // chrome), and the shell itself never scrolls (`overflow-hidden`). The fixed
    // sidebar and the scrollable <main> are independent scroll regions, so the
    // navigation is always visible while only content scrolls — no double
    // scrollbars, no layout shift. Overlays (dialogs/sheets/command palette/
    // toasts) portal to <body> above this at higher z-index, unaffected.
    <div className="flex h-dvh overflow-hidden bg-[var(--background)] text-[var(--foreground)]">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-[var(--radius-at-md)] focus:bg-[var(--card)] focus:px-3 focus:py-2 focus:shadow-[var(--shadow-at-e2)]"
      >
        Skip to content
      </a>

      {/* Fixed navigation — full height, scrolls internally only if it overflows. */}
      <Sidebar />

      {/* Content column: fills the remaining width; `min-h-0` lets the inner
          <main> own the vertical scroll instead of the shell. */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Pinned above the scroll region (never scroll away). */}
        <div className="shrink-0">
          <OfflineIndicator />
          <VerifyEmailBanner />
          {/* Mobile top bar */}
          <header className="flex h-14 items-center justify-between border-b border-[var(--border)] bg-[var(--card)] px-4 md:hidden">
            <Link href="/" className="flex items-center gap-2 font-semibold">
              <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] text-xs font-bold text-[var(--primary-foreground)]">
                FW
              </span>
              FitWright
            </Link>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Search"
                onClick={() => openCommandPalette()}
              >
                <Search className="h-[18px] w-[18px]" />
              </Button>
              <NotificationCenter />
              <ThemeToggle />
              <AccountMenu />
            </div>
          </header>
        </div>

        {/* The ONLY vertical scroll region. `tabIndex={-1}` makes the skip link
            able to move focus here; `overscroll-y-contain` stops scroll chaining. */}
        <main
          id="main-content"
          tabIndex={-1}
          className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain pb-20 focus:outline-none md:pb-0"
        >
          <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">{children}</div>
        </main>
      </div>

      <BottomNav />
    </div>
  );
}
