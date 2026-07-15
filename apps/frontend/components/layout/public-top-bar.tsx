'use client';

/** Public top bar for marketing/auth pages (Task 3.3 / Req 3.5). */
import * as React from 'react';
import Link from 'next/link';
import Menu from 'lucide-react/dist/esm/icons/menu';
import X from 'lucide-react/dist/esm/icons/x';
import LayoutDashboard from 'lucide-react/dist/esm/icons/layout-dashboard';
import { Button } from '@/components/atelier/button';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { AccountMenu } from '@/components/layout/account-menu';
import { useSession } from '@/lib/context/session';

/**
 * Anchor links use absolute `/#id` so they scroll correctly even from other
 * marketing pages (e.g. /privacy, /terms), not just the landing page.
 */
const NAV_LINKS: { href: string; label: string }[] = [
  { href: '/#how', label: 'How it works' },
  { href: '/#features', label: 'Features' },
  { href: '/resume-tailoring', label: 'Tailoring' },
  { href: '/#faq', label: 'FAQ' },
  { href: '/connect', label: 'Connect' },
  { href: '/contact', label: 'Contact' },
];

export function PublicTopBar() {
  const [open, setOpen] = React.useState(false);
  // Auth-aware: a signed-in visitor sees a "Dashboard" shortcut + their profile
  // menu (never Sign in/Sign up). A guest sees the auth entry points. During the
  // brief hydration window we render neither, to avoid flashing the wrong state.
  const { status } = useSession();
  const authed = status === 'authenticated';
  const resolved = status !== 'loading';

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--border)] bg-[var(--background)]/80 backdrop-blur">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-4 md:px-8">
        <Link
          href="/"
          className="flex items-center gap-2 font-semibold"
          onClick={() => setOpen(false)}
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] text-xs font-bold text-[var(--primary-foreground)]">
            FW
          </span>
          FitWright
        </Link>

        {/* Desktop nav */}
        <nav
          className="hidden items-center gap-6 text-sm text-[var(--muted-foreground)] md:flex"
          aria-label="Sections"
        >
          {NAV_LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="hover:text-[var(--foreground)]">
              {l.label}
            </Link>
          ))}
          <a
            href="https://github.com/ObaidGits/FitWRight"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-[var(--foreground)]"
          >
            GitHub
          </a>
        </nav>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          {resolved &&
            (authed ? (
              <>
                <Button asChild size="sm" className="hidden sm:inline-flex">
                  <Link href="/home">
                    <LayoutDashboard className="h-4 w-4" /> Dashboard
                  </Link>
                </Button>
                <AccountMenu />
              </>
            ) : (
              <>
                <Button asChild size="sm" variant="ghost" className="hidden sm:inline-flex">
                  <Link href="/login">Sign in</Link>
                </Button>
                <Button asChild size="sm" className="hidden sm:inline-flex">
                  <Link href="/signup">Sign up</Link>
                </Button>
              </>
            ))}
          {/* Mobile menu toggle */}
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
        </div>
      </div>

      {/* Mobile menu */}
      {open && (
        <nav
          className="border-t border-[var(--border)] bg-[var(--background)] md:hidden"
          aria-label="Sections"
        >
          <div className="mx-auto flex w-full max-w-6xl flex-col px-4 py-2">
            {NAV_LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="rounded-[var(--radius-at-md)] px-2 py-2.5 text-sm text-[var(--foreground)] hover:bg-[var(--accent)]"
              >
                {l.label}
              </Link>
            ))}
            <a
              href="https://github.com/ObaidGits/FitWRight"
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
              className="rounded-[var(--radius-at-md)] px-2 py-2.5 text-sm text-[var(--foreground)] hover:bg-[var(--accent)]"
            >
              GitHub
            </a>
            {resolved &&
              (authed ? (
                <div className="mt-2 flex flex-col gap-2">
                  <Button asChild>
                    <Link href="/home" onClick={() => setOpen(false)}>
                      <LayoutDashboard className="h-4 w-4" /> Go to dashboard
                    </Link>
                  </Button>
                  <Button asChild variant="outline">
                    <Link href="/settings" onClick={() => setOpen(false)}>
                      Settings
                    </Link>
                  </Button>
                </div>
              ) : (
                <div className="mt-2 flex flex-col gap-2">
                  <Button asChild variant="outline">
                    <Link href="/login" onClick={() => setOpen(false)}>
                      Sign in
                    </Link>
                  </Button>
                  <Button asChild>
                    <Link href="/signup" onClick={() => setOpen(false)}>
                      Sign up
                    </Link>
                  </Button>
                </div>
              ))}
          </div>
        </nav>
      )}
    </header>
  );
}
