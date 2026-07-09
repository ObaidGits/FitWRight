'use client';

/** Public top bar for marketing/auth pages (Task 3.3 / Req 3.5). */
import * as React from 'react';
import Link from 'next/link';
import Menu from 'lucide-react/dist/esm/icons/menu';
import X from 'lucide-react/dist/esm/icons/x';
import { Button } from '@/components/atelier/button';
import { ThemeToggle } from '@/components/theme/theme-toggle';

/**
 * Anchor links use absolute `/#id` so they scroll correctly even from other
 * marketing pages (e.g. /privacy, /terms), not just the landing page.
 */
const NAV_LINKS: { href: string; label: string }[] = [
  { href: '/#how', label: 'How it works' },
  { href: '/#features', label: 'Features' },
  { href: '/#faq', label: 'FAQ' },
];

export function PublicTopBar() {
  const [open, setOpen] = React.useState(false);

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
          <Button asChild size="sm" className="hidden sm:inline-flex">
            <Link href="/home">Get Started</Link>
          </Button>
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
            <Button asChild className="mt-2">
              <Link href="/home" onClick={() => setOpen(false)}>
                Get Started
              </Link>
            </Button>
          </div>
        </nav>
      )}
    </header>
  );
}
