/**
 * Admin shell (Task 15 / Req 8, auth wiring Task 8.1).
 *
 * SSR-authoritative guard: in hosted mode an unauthenticated visitor is sent to
 * `/login?next=...` and a non-admin to `/home` before any content renders. The
 * backend independently enforces the admin capability on every admin API
 * (Req 11.2) - hiding the UI is never the boundary. In `SINGLE_USER_MODE` the
 * owner is admin, so local dev is unchanged.
 */
import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { headers } from 'next/headers';
import { AdminShell } from '@/components/admin/admin-shell';
import { getServerSession } from '@/lib/api/session-server';
import { NOINDEX } from '@/lib/seo/metadata';

// Admin console - never indexable.
export const metadata: Metadata = { robots: NOINDEX };

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const session = await getServerSession();
  if (!session.resolved) {
    // A transient session-store/backend outage is not evidence of logout or
    // lost admin rights. Preserve the cookie and render the error boundary.
    throw new Error('Authentication service is temporarily unavailable.');
  }
  if (!session.user) {
    const hdrs = await headers();
    const path = hdrs.get('x-invoke-path') || hdrs.get('x-pathname') || '/admin';
    redirect(`/login?next=${encodeURIComponent(path)}`);
  }
  if (session.user.role !== 'admin') {
    redirect('/home');
  }
  return <AdminShell>{children}</AdminShell>;
}
