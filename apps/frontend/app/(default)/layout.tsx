import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { headers } from 'next/headers';
import { ResumePreviewProvider } from '@/components/common/resume_previewer_context';
import { StatusCacheProvider } from '@/lib/context/status-cache';
import { LanguageProvider } from '@/lib/context/language-context';
import { LocalizedErrorBoundary } from '@/components/common/error-boundary';
import { getServerSession } from '@/lib/api/session-server';
import { NOINDEX } from '@/lib/seo/metadata';

// Authenticated resume builder — never indexable.
export const metadata: Metadata = { robots: NOINDEX };

export default async function DefaultLayout({ children }: { children: React.ReactNode }) {
  // Authoritative SSR guard, mirroring the (app) group: the advanced editor
  // (/builder) is a first-class part of the authenticated app, so an
  // unauthenticated hosted visitor is redirected to /login before any content
  // renders (no protected-shell flash). In SINGLE_USER_MODE getServerSession
  // resolves the owner, so local dev is unchanged. The edge middleware performs
  // the same presence check even earlier; the backend remains the real boundary.
  const session = await getServerSession();
  if (!session.resolved) {
    // A transient auth-store outage is not proof that the session expired.
    // Preserve the cookie and surface the route error boundary, matching the
    // main app/admin guards instead of rendering an unauthenticated builder.
    throw new Error('Authentication service is temporarily unavailable.');
  }
  if (!session.user) {
    const hdrs = await headers();
    const path = hdrs.get('x-invoke-path') || hdrs.get('x-pathname') || '/builder';
    redirect(`/login?next=${encodeURIComponent(path)}`);
  }

  return (
    <StatusCacheProvider>
      <LanguageProvider>
        <ResumePreviewProvider>
          <LocalizedErrorBoundary>
            {/* The builder (the only (default) route) is fully migrated to the
                Atelier design system, whose tokens are scoped under `.atelier`
                (see styles/atelier.css). Wrapping here provides those tokens —
                matching the (app) group — so light/dark render correctly. */}
            <main className="atelier flex min-h-screen flex-col bg-[var(--background)] text-[var(--foreground)]">
              {children}
            </main>
          </LocalizedErrorBoundary>
        </ResumePreviewProvider>
      </LanguageProvider>
    </StatusCacheProvider>
  );
}
