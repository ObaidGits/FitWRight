'use client';

/** Mobile bottom navigation (Req 28.2) — Home · Resumes · [Tailor] · Applications. */
import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import { cn } from '@/lib/utils';
import { PRIMARY_NAV, TAILOR_HREF } from '@/components/layout/nav-items';

type NavItem = (typeof PRIMARY_NAV)[number];

// Module-level component (not created during render) — receives `active` as a
// prop so it stays a stable component identity across renders.
function NavTab({ item, active }: { item: NavItem; active: boolean }) {
  const { href, label, icon: Icon } = item;
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] font-medium',
        active ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]'
      )}
    >
      <Icon className="h-5 w-5" />
      {label}
    </Link>
  );
}

export function BottomNav() {
  const pathname = usePathname();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  const [home, resumes, applications] = [PRIMARY_NAV[0], PRIMARY_NAV[1], PRIMARY_NAV[2]];

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-40 flex items-center border-t border-[var(--border)] bg-[var(--card)] pb-[env(safe-area-inset-bottom)] md:hidden"
      aria-label="Primary"
    >
      <NavTab item={home} active={isActive(home.href)} />
      <NavTab item={resumes} active={isActive(resumes.href)} />
      <Link
        href={TAILOR_HREF}
        aria-label="Tailor to a job"
        className="mx-1 flex flex-col items-center justify-center"
      >
        <span className="-mt-5 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] shadow-[var(--shadow-at-e2)]">
          <Sparkles className="h-5 w-5" />
        </span>
        <span className="text-[11px] font-medium text-[var(--muted-foreground)]">Tailor</span>
      </Link>
      <NavTab item={applications} active={isActive(applications.href)} />
    </nav>
  );
}
