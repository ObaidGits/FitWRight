/**
 * Authenticated app route group (Task 3 + auth wiring Task 8.1).
 *
 * SSR-authoritative guard: reads the session server-side (per-request) and, in
 * hosted mode, redirects an unauthenticated visitor to `/login?next=…` BEFORE
 * any content renders — so there is never an unauthenticated flash. In
 * `SINGLE_USER_MODE` the owner is always resolved, so local dev is unchanged.
 *
 * `middleware.ts` performs the same presence check even earlier (edge, no
 * backend call) as a fast path; this layout is the authoritative server check.
 * Client guards remain UX-only — the backend enforces `user_id` on every call.
 */
import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { headers } from 'next/headers';
import { AppShell } from '@/components/layout/app-shell';
import { getServerSession } from '@/lib/api/session-server';
import { ResilienceProvider } from '@/components/resilience/resilience-provider';
import { NOINDEX } from '@/lib/seo/metadata';

// Authenticated, per-user workspace — never indexable.
export const metadata: Metadata = { robots: NOINDEX };

export default async function AppGroupLayout({ children }: { children: React.ReactNode }) {
  const user = await getServerSession();
  if (!user) {
    const hdrs = await headers();
    const path = hdrs.get('x-invoke-path') || hdrs.get('x-pathname') || '/home';
    redirect(`/login?next=${encodeURIComponent(path)}`);
  }
  return (
    <div className="atelier">
      <ResilienceProvider>
        <AppShell>{children}</AppShell>
      </ResilienceProvider>
    </div>
  );
}
