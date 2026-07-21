/** Auth route group (Task 5) - centered-card layout, Atelier-scoped. */
import type { Metadata } from 'next';
import Link from 'next/link';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import { NOINDEX } from '@/lib/seo/metadata';

// Authentication flows - never indexable.
export const metadata: Metadata = { robots: NOINDEX };

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="atelier flex min-h-screen flex-col bg-[var(--background)] text-[var(--foreground)]">
      <header className="flex h-16 items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] text-xs font-bold text-[var(--primary-foreground)]">
            FW
          </span>
          FitWright
        </Link>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-at-md)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-4 w-4" /> Back to home
        </Link>
      </header>
      <main className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-sm">{children}</div>
      </main>
    </div>
  );
}
